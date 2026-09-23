from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from ..runtime_types import RunContext, ToolCall, ToolResult, ToolSpec
from ..safe_http import SafeHttpTransport


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


class _MCPClientProvider:
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

    @asynccontextmanager
    async def client(self) -> AsyncIterator[Any]:
        if isinstance(self.target, str) and self.target.startswith(("http://", "https://")):
            self.http_guard.endpoint_validator.assert_safe_endpoint(
                self.target,
                require_resolved=True,
            )
        if self.client_factory is None:
            try:
                from mcp import Client
            except ImportError as exc:
                raise RuntimeError(
                    "Install the MCP integration with: pip install 'agentweave-router[mcp]'"
                ) from exc
            cm = Client(self.target)
        else:
            cm = self.client_factory(self.target)
        async with cm as client:
            yield client


class MCPToolCatalog:
    """Real MCP catalog backed by the official MCP Python SDK client."""

    def __init__(
        self,
        target: Any,
        *,
        client_factory: Callable[[Any], Any] | None = None,
        http_guard: SafeHttpTransport | None = None,
        source: str | None = None,
    ) -> None:
        self.provider = _MCPClientProvider(
            target,
            client_factory=client_factory,
            http_guard=http_guard,
        )
        self.source = source or (target if isinstance(target, str) else "mcp")

    async def list_tools(self, context: RunContext) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        async with self.provider.client() as client:
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
                    tools.append(
                        ToolSpec(
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
                    )
                cursor = getattr(page, "next_cursor", None)
                if cursor is None:
                    break
        return tools


class MCPExecutor:
    """Execute normalized ToolCalls through an MCP client."""

    def __init__(
        self,
        target: Any,
        *,
        client_factory: Callable[[Any], Any] | None = None,
        http_guard: SafeHttpTransport | None = None,
    ) -> None:
        self.provider = _MCPClientProvider(
            target,
            client_factory=client_factory,
            http_guard=http_guard,
        )

    async def execute(self, call: ToolCall, context: RunContext) -> ToolResult:
        try:
            async with self.provider.client() as client:
                raw = await client.call_tool(call.name, dict(call.arguments))
            is_error = bool(getattr(raw, "is_error", False))
            content = _dump(getattr(raw, "content", None))
            structured = _dump(getattr(raw, "structured_content", None))
            meta = _dump(getattr(raw, "meta", None)) or {}
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
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
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
