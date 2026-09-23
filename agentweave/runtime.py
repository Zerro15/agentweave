from __future__ import annotations

import inspect
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from agentweave_security.authorization import (
    AuthorizationDecision,
    AuthorizationGate,
    AuthorizationPolicy,
)
from agentweave_security.scope_filter import ScopeContext, ScopedTool, StaticScopeFilter

from .runtime_types import (
    ModelResponse,
    RunContext,
    RuntimeResult,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class CatalogProvider(Protocol):
    async def list_tools(self, context: RunContext) -> Sequence[ToolSpec]:
        ...


class ToolSearchProvider(Protocol):
    async def search(
        self,
        text: str,
        *,
        context: RunContext,
        excluded_names: set[str],
        limit: int,
    ) -> Sequence[ToolSpec]:
        ...


class Executor(Protocol):
    async def execute(self, call: ToolCall, context: RunContext) -> ToolResult:
        ...


class ScopePolicy(Protocol):
    async def filter(
        self,
        tools: Sequence[ToolSpec],
        context: RunContext,
    ) -> tuple[list[ToolSpec], Mapping[str, Any]]:
        ...


@dataclass(frozen=True)
class RoutingPreview:
    selected: tuple[ToolSpec, ...]
    permitted: tuple[ToolSpec, ...]
    confidence: float
    abstained: bool
    provenance: Mapping[str, Any]


class StaticToolCatalog:
    def __init__(self, tools: Sequence[ToolSpec]):
        self.tools = list(tools)

    async def list_tools(self, context: RunContext) -> Sequence[ToolSpec]:
        return list(self.tools)


class CallableExecutor:
    """Execute normalized ToolCalls against sync/async Python callables."""

    def __init__(self, functions: Mapping[str, Callable[..., Any]]):
        self.functions = dict(functions)

    async def execute(self, call: ToolCall, context: RunContext) -> ToolResult:
        fn = self.functions.get(call.name)
        if fn is None:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                success=False,
                error="unknown-tool",
            )
        try:
            result = fn(**dict(call.arguments))
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                success=True,
                content=result,
                structured_content=result if isinstance(result, (dict, list)) else None,
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )


class DefaultScopePolicy:
    """Fail-closed deterministic role/tenant/permission/scope filter."""

    def __init__(self, scope_filter: StaticScopeFilter | None = None):
        self.scope_filter = scope_filter or StaticScopeFilter(policy_version="runtime-scope-v1")

    @staticmethod
    def _scoped(tool: ToolSpec) -> ScopedTool:
        return ScopedTool(
            name=tool.name,
            roles=tool.roles,
            tenants=tool.tenants,
            permissions=tool.permissions,
            scopes=tool.scopes,
            environments=tool.environments,
            metadata={
                **dict(tool.metadata),
                "risk_level": tool.risk_level,
                "provider": tool.provider,
                "source": tool.source,
            },
        )

    async def filter(
        self,
        tools: Sequence[ToolSpec],
        context: RunContext,
    ) -> tuple[list[ToolSpec], Mapping[str, Any]]:
        scoped = [self._scoped(tool) for tool in tools]
        result = self.scope_filter.filter(
            scoped,
            ScopeContext(
                role=context.role,
                tenant=context.tenant,
                identity=context.identity,
                permissions=context.permissions,
                scopes=context.scopes,
                environment=context.environment,
            ),
        )
        allowed_names = {tool.name for tool in result.tools}
        allowed = [tool for tool in tools if tool.name in allowed_names]
        return allowed, {
            "policy_version": result.provenance.policy_version,
            "source_catalog_hash": result.provenance.source_catalog_hash,
            "source_catalog_size": result.provenance.source_catalog_size,
            "resulting_catalog_hash": result.provenance.resulting_catalog_hash,
            "resulting_catalog_size": result.provenance.resulting_catalog_size,
            "decisions": [
                {
                    "tool": decision.tool,
                    "allowed": decision.allowed,
                    "reason_code": decision.reason_code,
                }
                for decision in result.decisions
            ],
        }


class RuntimeAuthorizationPolicy:
    """Default post-selection authorization policy."""

    def authorize(
        self,
        *,
        action: str,
        context: Mapping[str, object],
    ) -> AuthorizationDecision:
        allowed = set(context.get("model_visible_tools") or ())
        if action not in allowed:
            return AuthorizationDecision(False, "tool-not-model-visible")
        risks = context.get("tool_risks") or {}
        risk = (
            str(risks.get(action, "standard")).lower()
            if isinstance(risks, Mapping)
            else "standard"
        )
        if risk in {"high", "critical"} and not bool(context.get("human_approved")):
            return AuthorizationDecision(False, "human-approval-required")
        return AuthorizationDecision(True, "allowed")


def _parse_arguments(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw": value}
        return parsed if isinstance(parsed, Mapping) else {"_value": parsed}
    return {"_value": value}


def _parse_tool_calls(items: Any, provider: str | None = None) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for item in items or ():
        if isinstance(item, Mapping):
            fn = item.get("function") if isinstance(item.get("function"), Mapping) else item
            name = fn.get("name") or item.get("name")
            if not name:
                continue
            calls.append(
                ToolCall(
                    id=str(item.get("id") or uuid.uuid4()),
                    name=str(name),
                    arguments=_parse_arguments(fn.get("arguments", item.get("arguments"))),
                    provider=provider,
                    raw=item,
                )
            )
            continue
        name = getattr(getattr(item, "function", None), "name", None) or getattr(
            item, "name", None
        )
        if not name:
            continue
        fn = getattr(item, "function", None)
        args = (
            getattr(fn, "arguments", None)
            if fn is not None
            else getattr(item, "arguments", None)
        )
        calls.append(
            ToolCall(
                id=str(getattr(item, "id", None) or uuid.uuid4()),
                name=str(name),
                arguments=_parse_arguments(args),
                provider=provider,
                raw=item,
            )
        )
    return tuple(calls)


def normalize_model_response(raw: Any) -> ModelResponse:
    if isinstance(raw, ModelResponse):
        return raw

    if isinstance(raw, Mapping):
        usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
        if isinstance(raw.get("choices"), Sequence) and raw.get("choices"):
            choice = raw["choices"][0]
            if isinstance(choice, Mapping):
                message = (
                    choice.get("message")
                    if isinstance(choice.get("message"), Mapping)
                    else {}
                )
                return ModelResponse(
                    text=message.get("content"),
                    tool_calls=_parse_tool_calls(message.get("tool_calls")),
                    finish_reason=choice.get("finish_reason"),
                    usage=dict(usage),
                    raw=raw,
                )
        return ModelResponse(
            text=raw.get("text") or raw.get("content") or raw.get("output_text"),
            tool_calls=_parse_tool_calls(raw.get("tool_calls")),
            finish_reason=raw.get("finish_reason"),
            usage=dict(usage),
            raw=raw,
        )

    choices = getattr(raw, "choices", None)
    if choices:
        choice = choices[0]
        message = getattr(choice, "message", None)
        return ModelResponse(
            text=getattr(message, "content", None),
            tool_calls=_parse_tool_calls(getattr(message, "tool_calls", None)),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=getattr(raw, "usage", {}) or {},
            raw=raw,
        )
    return ModelResponse(text=str(raw) if raw is not None else None, raw=raw)


class AgentWeaveRuntime:
    """Canonical secure AgentWeave runtime.

    Pipeline: catalog -> scope -> routing -> model -> authorization -> executor
    -> recovery/rediscovery -> model continuation.

    Deferred-discovery candidates always pass through scope policy before they can be
    routed or exposed to the model.
    """

    def __init__(
        self,
        *,
        model: Any,
        catalog: CatalogProvider,
        executor: Executor,
        router: Any | None = None,
        scope_policy: ScopePolicy | None = None,
        authorization_policy: AuthorizationPolicy | None = None,
        search_provider: ToolSearchProvider | None = None,
        max_tools: int = 8,
        search_limit: int = 16,
        max_model_turns: int = 4,
        max_recovery_attempts: int = 2,
    ) -> None:
        if router is None:
            from agentweave_byom.tool_routing import AdaptiveRouter, DeterministicRouterV1

            router = AdaptiveRouter(DeterministicRouterV1())
        self.model = model
        self.catalog = catalog
        self.executor = executor
        self.router = router
        self.scope_policy = scope_policy or DefaultScopePolicy()
        self.authorization_policy = authorization_policy or RuntimeAuthorizationPolicy()
        self.search_provider = search_provider
        self.max_tools = max(1, int(max_tools))
        self.search_limit = max(1, int(search_limit))
        self.max_model_turns = max(1, int(max_model_turns))
        self.max_recovery_attempts = max(0, int(max_recovery_attempts))

    @staticmethod
    def _dedupe(tools: Sequence[ToolSpec]) -> list[ToolSpec]:
        seen: set[str] = set()
        out: list[ToolSpec] = []
        for tool in tools:
            key = tool.name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(tool)
        return out

    async def _route_raw(self, text: str, tools: Sequence[ToolSpec]):
        descriptors = [tool.to_function_tool() for tool in tools]
        method = getattr(self.router, "aroute", None) or getattr(self.router, "route", None)
        if method is None:
            raise TypeError("router must define route() or aroute()")
        result = method(text, descriptors, max_tools=self.max_tools)
        if inspect.isawaitable(result):
            result = await result
        return result

    @staticmethod
    def _selected_specs(tools: Sequence[ToolSpec], routing: Any) -> list[ToolSpec]:
        names = []
        for item in getattr(routing, "selected", ()):
            if isinstance(item, Mapping):
                fn = item.get("function") if isinstance(item.get("function"), Mapping) else item
                name = fn.get("name")
                if name:
                    names.append(str(name).lower())
        index = {tool.name.lower(): tool for tool in tools}
        return [index[name] for name in names if name in index]

    async def preview_route(
        self,
        text: str,
        *,
        context: RunContext | None = None,
        tools: Sequence[ToolSpec] | None = None,
    ) -> RoutingPreview:
        ctx = context or RunContext()
        source = self._dedupe(
            list(tools) if tools is not None else list(await self.catalog.list_tools(ctx))
        )
        permitted, scope_provenance = await self.scope_policy.filter(source, ctx)
        routing = await self._route_raw(text, permitted)

        search_provenance: dict[str, Any] = {
            "search_invoked": False,
            "discovered": 0,
            "discovered_permitted": 0,
        }
        if bool(getattr(routing, "abstained", False)) and self.search_provider is not None:
            discovered = list(
                await self.search_provider.search(
                    text,
                    context=ctx,
                    excluded_names={tool.name.lower() for tool in source},
                    limit=self.search_limit,
                )
                or []
            )
            discovered = self._dedupe(discovered)
            discovered_permitted, discovered_scope = await self.scope_policy.filter(
                discovered,
                ctx,
            )
            permitted = self._dedupe([*permitted, *discovered_permitted])
            routing = await self._route_raw(text, permitted)
            search_provenance = {
                "search_invoked": True,
                "discovered": len(discovered),
                "discovered_permitted": len(discovered_permitted),
                "discovered_scope": dict(discovered_scope),
            }

        selected = self._selected_specs(permitted, routing)
        provenance = {
            "scope": dict(scope_provenance),
            "routing": dict(getattr(routing, "provenance", {}) or {}),
            "deferred_search": search_provenance,
            "source_catalog_size": len(source),
            "permitted_catalog_size": len(permitted),
            "selected_tools": [tool.name for tool in selected],
        }
        return RoutingPreview(
            selected=tuple(selected),
            permitted=tuple(permitted),
            confidence=float(getattr(routing, "confidence", 1.0)),
            abstained=bool(getattr(routing, "abstained", False)),
            provenance=provenance,
        )

    async def _complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolSpec],
        model_kwargs: Mapping[str, Any] | None,
    ) -> ModelResponse:
        raw = self.model.complete(
            messages,
            tools=[tool.to_function_tool() for tool in tools],
            **dict(model_kwargs or {}),
        )
        if inspect.isawaitable(raw):
            raw = await raw
        return normalize_model_response(raw)

    @staticmethod
    def _assistant_tool_message(response: ModelResponse) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": response.text or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(dict(call.arguments), sort_keys=True),
                    },
                }
                for call in response.tool_calls
            ],
        }

    @staticmethod
    def _tool_message(result: ToolResult) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "name": result.name,
            "content": result.model_content(),
        }

    async def run(
        self,
        text: str,
        *,
        context: RunContext | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        model_kwargs: Mapping[str, Any] | None = None,
    ) -> RuntimeResult:
        ctx = context or RunContext()
        source = self._dedupe(list(await self.catalog.list_tools(ctx)))
        active_source = list(source)
        conversation = list(messages or [{"role": "user", "content": text}])
        tool_results: list[ToolResult] = []
        recovery_attempts = 0
        last_response: ModelResponse | None = None
        last_preview: RoutingPreview | None = None
        failed_names: set[str] = set()
        run_provenance: dict[str, Any] = {"turns": []}

        for turn in range(self.max_model_turns):
            candidates = [
                tool for tool in active_source if tool.name.lower() not in failed_names
            ]
            preview = await self.preview_route(text, context=ctx, tools=candidates)
            last_preview = preview
            response = await self._complete(
                conversation,
                preview.selected,
                model_kwargs,
            )
            last_response = response
            run_provenance["turns"].append(
                {
                    "turn": turn + 1,
                    "selected_tools": [tool.name for tool in preview.selected],
                    "routing_confidence": preview.confidence,
                    "routing_abstained": preview.abstained,
                    "routing": dict(preview.provenance),
                    "tool_calls": [call.name for call in response.tool_calls],
                }
            )

            if not response.tool_calls:
                return RuntimeResult(
                    status="completed",
                    response=response,
                    tool_results=tuple(tool_results),
                    selected_tools=tuple(tool.name for tool in preview.selected),
                    routing_confidence=preview.confidence,
                    routing_abstained=preview.abstained,
                    recovery_attempts=recovery_attempts,
                    provenance=run_provenance,
                )

            conversation.append(self._assistant_tool_message(response))
            visible = {tool.name for tool in preview.selected}
            tool_risks = {tool.name: tool.risk_level for tool in preview.selected}
            gate = AuthorizationGate(self.authorization_policy)
            turn_failed = False

            for call in response.tool_calls:
                decision = gate.authorize(
                    action=call.name,
                    context={
                        "identity": ctx.identity,
                        "role": ctx.role,
                        "tenant": ctx.tenant,
                        "permissions": ctx.permissions,
                        "scopes": ctx.scopes,
                        "environment": ctx.environment,
                        "risk_tier": ctx.risk_tier,
                        "human_approved": ctx.human_approved,
                        "model_visible_tools": visible,
                        "tool_risks": tool_risks,
                        "metadata": ctx.metadata,
                    },
                )
                if not decision.allowed:
                    result = ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        success=False,
                        error=f"authorization-denied:{decision.reason_code}",
                    )
                else:
                    result = await self.executor.execute(call, ctx)
                tool_results.append(result)
                conversation.append(self._tool_message(result))
                if not result.success:
                    turn_failed = True
                    failed_names.add(call.name.lower())

            if turn_failed:
                recovery_attempts += 1
                if recovery_attempts > self.max_recovery_attempts:
                    return RuntimeResult(
                        status="needs-review",
                        response=response,
                        tool_results=tuple(tool_results),
                        selected_tools=tuple(tool.name for tool in preview.selected),
                        routing_confidence=preview.confidence,
                        routing_abstained=preview.abstained,
                        recovery_attempts=recovery_attempts,
                        provenance=run_provenance,
                    )
                continue

        return RuntimeResult(
            status="max-turns",
            response=last_response,
            tool_results=tuple(tool_results),
            selected_tools=tuple(
                tool.name for tool in (last_preview.selected if last_preview else ())
            ),
            routing_confidence=last_preview.confidence if last_preview else None,
            routing_abstained=last_preview.abstained if last_preview else False,
            recovery_attempts=recovery_attempts,
            provenance=run_provenance,
        )
