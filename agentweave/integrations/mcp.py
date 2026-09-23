from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..runtime_types import RunContext, ToolCall, ToolResult, ToolSpec
from ..safe_http import SafeHttpTransport


def _default_source(target: Any) -> str:
    """Return a stable implicit source that cannot expose URL credentials or secrets."""

    if not isinstance(target, str):
        return "mcp"
    parsed = urlsplit(target)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return target
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{parsed.port}" if parsed.port is not None else "")
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, (str, int, float, bool, dict, list, tuple)):
        return value
    if hasattr(value, "__dict__"):
        return {
            key: _dump(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _string_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in value if item is not None)
    return frozenset()


def _mcp_policy(tool: ToolSpec) -> ToolSpec:
    """Map opt-in AgentWeave MCP metadata into restrictive policy fields.

    Servers may add ``meta.agentweave`` restrictions.  Destructive annotations can
    only elevate risk; untrusted metadata cannot lower the default risk level.
    """

    meta = tool.metadata.get("meta") if isinstance(tool.metadata, Mapping) else None
    annotations = (
        tool.metadata.get("annotations") if isinstance(tool.metadata, Mapping) else None
    )
    policy = {}
    if isinstance(meta, Mapping) and isinstance(meta.get("agentweave"), Mapping):
        policy = dict(meta["agentweave"])

    risk = tool.risk_level
    requested_risk = str(policy.get("risk_level", "")).lower()
    if requested_risk in {"high", "critical"}:
        risk = requested_risk
    if isinstance(annotations, Mapping):
        destructive = annotations.get("destructiveHint")
        if destructive is None:
            destructive = annotations.get("destructive_hint")
        if destructive is True and risk not in {"critical"}:
            risk = "high"

    return replace(
        tool,
        risk_level=risk,
        permissions=tool.permissions | _string_set(policy.get("permissions")),
        scopes=tool.scopes | _string_set(policy.get("scopes")),
        roles=tool.roles | _string_set(policy.get("roles")),
        tenants=tool.tenants | _string_set(policy.get("tenants")),
        environments=tool.environments | _string_set(policy.get("environments")),
    )


class MCPConnection:
    """Reusable, lifecycle-owned MCP client connection.

    HTTP targets are revalidated before every connection establishment.  The MCP SDK
    still owns its protocol transport; AgentWeave therefore treats this as endpoint
    validation plus session lifecycle rather than claiming SafeHttpTransport owns the
    SDK's wire connection.
    """

    def __init__(
        self,
        target: Any,
        *,
        client_factory: Callable[[Any], Any] | None = None,
        http_guard: SafeHttpTransport | None = None,
    ) -> None:
        self.target = target
        self.client_factory = client_factory
        self.http_guard = http_guard or SafeHttpTransport()
        self._cm: Any = None
        self._client: Any = None
        self._lock = asyncio.Lock()

    def _validate_target(self) -> None:
        if isinstance(self.target, str) and self.target.startswith(("http://", "https://")):
            self.http_guard.endpoint_validator.assert_safe_endpoint(
                self.target,
                require_resolved=True,
            )

    def _context_manager(self) -> Any:
        self._validate_target()
        if self.client_factory is None:
            try:
                from mcp import Client
            except ImportError as exc:
                raise RuntimeError(
                    "Install the MCP integration with: pip install 'agentweave-router[mcp]'"
                ) from exc
            return Client(self.target)
        return self.client_factory(self.target)

    async def start(self) -> None:
        async with self._lock:
            if self._client is not None:
                return
            cm = self._context_manager()
            self._client = await cm.__aenter__()
            self._cm = cm

    async def stop(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            cm = self._cm
            self._client = None
            self._cm = None
            if cm is not None:
                await cm.__aexit__(None, None, None)

    @asynccontextmanager
    async def client(self) -> AsyncIterator[Any]:
        if self._client is not None:
            self._validate_target()
            yield self._client
            return
        cm = self._context_manager()
        async with cm as client:
            yield client


class MCPToolCatalog:
    """Real MCP catalog backed by the official MCP Python SDK client."""

    def __init__(
        self,
        target: Any | None = None,
        *,
        connection: MCPConnection | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        http_guard: SafeHttpTransport | None = None,
        source: str | None = None,
        policy_mapper: Callable[[ToolSpec], ToolSpec] | None = _mcp_policy,
    ) -> None:
        if connection is None and target is None:
            raise ValueError("target or connection is required")
        self.connection = connection or MCPConnection(
            target,
            client_factory=client_factory,
            http_guard=http_guard,
        )
        self.provider = self.connection  # backward-compatible attribute
        resolved_target = self.connection.target
        self.source = source or _default_source(resolved_target)
        self.policy_mapper = policy_mapper

    async def start(self) -> None:
        await self.connection.start()

    async def stop(self) -> None:
        await self.connection.stop()

    async def list_tools(self, context: RunContext) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        async with self.connection.client() as client:
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor)
                for tool in page.tools:
                    metadata = {
                        "title": getattr(tool, "title", None),
                        "annotations": _dump(getattr(tool, "annotations", None)),
                        "output_schema": _dump(getattr(tool, "output_schema", None)),
                        "meta": _dump(getattr(tool, "meta", None)),
                    }
                    spec = ToolSpec(
                        id=f"mcp:{self.source}:{tool.name}",
                        name=str(tool.name),
                        description=str(getattr(tool, "description", "") or ""),
                        input_schema=_dump(
                            getattr(tool, "input_schema", None)
                            or {"type": "object", "properties": {}}
                        ),
                        provider="mcp",
                        source=str(self.source),
                        metadata={
                            key: value
                            for key, value in metadata.items()
                            if value is not None
                        },
                        native=tool,
                    )
                    if self.policy_mapper is not None:
                        spec = self.policy_mapper(spec)
                    tools.append(spec)
                cursor = getattr(page, "next_cursor", None)
                if cursor is None:
                    break
        return tools


class MCPExecutor:
    """Execute normalized ToolCalls through a reusable MCP connection."""

    def __init__(
        self,
        target: Any | None = None,
        *,
        connection: MCPConnection | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        http_guard: SafeHttpTransport | None = None,
    ) -> None:
        if connection is None and target is None:
            raise ValueError("target or connection is required")
        self.connection = connection or MCPConnection(
            target,
            client_factory=client_factory,
            http_guard=http_guard,
        )
        self.provider = self.connection

    async def start(self) -> None:
        await self.connection.start()

    async def stop(self) -> None:
        await self.connection.stop()

    async def execute(self, call: ToolCall, context: RunContext) -> ToolResult:
        try:
            async with self.connection.client() as client:
                raw = await client.call_tool(call.name, dict(call.arguments))
            is_error = bool(getattr(raw, "is_error", False))
            content = _dump(getattr(raw, "content", None))
            structured = _dump(getattr(raw, "structured_content", None))
            meta = _dump(getattr(raw, "meta", None)) or {}
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                model_name=call.model_name,
                tool_key=call.tool_key,
                success=not is_error,
                content=content,
                structured_content=structured,
                error="mcp-tool-error" if is_error else None,
                metadata=meta if isinstance(meta, dict) else {"meta": meta},
                raw=raw,
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                model_name=call.model_name,
                tool_key=call.tool_key,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
