from __future__ import annotations

import inspect
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

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
    RuntimeTelemetry,
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


class ToolAuthorizationPolicy(Protocol):
    """Optional richer authorization hook with access to arguments and ToolSpec."""

    def authorize_tool(
        self,
        *,
        call: ToolCall,
        tool: ToolSpec,
        context: Mapping[str, object],
    ) -> AuthorizationDecision:
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
                model_name=call.model_name,
                tool_key=call.tool_key,
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
                model_name=call.model_name,
                tool_key=call.tool_key,
                success=True,
                content=result,
                structured_content=result if isinstance(result, (dict, list)) else None,
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


class DefaultScopePolicy:
    """Fail-closed deterministic role/tenant/permission/scope filter."""

    def __init__(self, scope_filter: StaticScopeFilter | None = None):
        self.scope_filter = scope_filter or StaticScopeFilter(policy_version="runtime-scope-v1")

    @staticmethod
    def _scoped(tool: ToolSpec) -> ScopedTool:
        return ScopedTool(
            name=tool.name,
            key=tool.key,
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
                "model_name": tool.exposed_name,
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
        allowed_keys = {tool.identity for tool in result.tools}
        allowed = [tool for tool in tools if tool.key in allowed_keys]
        return allowed, {
            "policy_version": result.provenance.policy_version,
            "source_catalog_hash": result.provenance.source_catalog_hash,
            "source_catalog_size": result.provenance.source_catalog_size,
            "resulting_catalog_hash": result.provenance.resulting_catalog_hash,
            "resulting_catalog_size": result.provenance.resulting_catalog_size,
            "decisions": [
                {
                    "tool": decision.tool,
                    "tool_key": decision.tool_key,
                    "allowed": decision.allowed,
                    "reason_code": decision.reason_code,
                }
                for decision in result.decisions
            ],
        }


class RuntimeAuthorizationPolicy:
    """Default post-selection authorization policy.

    Tool arguments and full ToolSpec metadata are included in ``context`` so custom
    policies can make resource/amount/path-aware decisions without changing the
    executor contract.
    """

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

    def authorize_tool(
        self,
        *,
        call: ToolCall,
        tool: ToolSpec,
        context: Mapping[str, object],
    ) -> AuthorizationDecision:
        return self.authorize(action=tool.exposed_name, context=context)


def _parse_arguments(value: Any) -> tuple[Mapping[str, Any], str | None]:
    if value is None:
        return {}, None
    if isinstance(value, Mapping):
        return dict(value), None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            return {}, f"invalid-json:{exc.msg}"
        if isinstance(parsed, Mapping):
            return dict(parsed), None
        return {}, "arguments-must-be-object"
    return {}, "arguments-must-be-object"


def _parse_tool_calls(items: Any, provider: str | None = None) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for item in items or ():
        if isinstance(item, Mapping):
            fn = item.get("function") if isinstance(item.get("function"), Mapping) else item
            name = fn.get("name") or item.get("name")
            if not name:
                continue
            arguments, parse_error = _parse_arguments(
                fn.get("arguments", item.get("arguments"))
            )
            calls.append(
                ToolCall(
                    id=str(item.get("id") or uuid.uuid4()),
                    name=str(name),
                    model_name=str(name),
                    arguments=arguments,
                    provider=provider,
                    parse_error=parse_error,
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
        arguments, parse_error = _parse_arguments(args)
        calls.append(
            ToolCall(
                id=str(getattr(item, "id", None) or uuid.uuid4()),
                name=str(name),
                model_name=str(name),
                arguments=arguments,
                provider=provider,
                parse_error=parse_error,
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
        usage = getattr(raw, "usage", {}) or {}
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        return ModelResponse(
            text=getattr(message, "content", None),
            tool_calls=_parse_tool_calls(getattr(message, "tool_calls", None)),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=usage if isinstance(usage, Mapping) else {},
            raw=raw,
        )
    return ModelResponse(text=str(raw) if raw is not None else None, raw=raw)


class AgentWeaveRuntime:
    """Canonical secure AgentWeave runtime.

    Pipeline: catalog -> scope -> routing -> model -> schema validation ->
    authorization -> executor -> recovery/rediscovery -> model continuation.

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
        authorization_policy: AuthorizationPolicy | ToolAuthorizationPolicy | None = None,
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
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        seen: set[int] = set()
        for component in (self.model, self.catalog, self.executor, self.search_provider):
            if component is None or id(component) in seen:
                continue
            seen.add(id(component))
            hook = getattr(component, "start", None)
            if hook is not None:
                result = hook()
                if inspect.isawaitable(result):
                    await result
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        seen: set[int] = set()
        for component in reversed((self.model, self.catalog, self.executor, self.search_provider)):
            if component is None or id(component) in seen:
                continue
            seen.add(id(component))
            hook = getattr(component, "stop", None)
            if hook is not None:
                result = hook()
                if inspect.isawaitable(result):
                    await result
        self._started = False

    async def __aenter__(self) -> "AgentWeaveRuntime":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.stop()
        return False

    @staticmethod
    def _dedupe(tools: Sequence[ToolSpec]) -> list[ToolSpec]:
        seen: set[str] = set()
        out: list[ToolSpec] = []
        for tool in tools:
            if tool.key in seen:
                continue
            seen.add(tool.key)
            out.append(tool)
        return out

    @staticmethod
    def _validate_exposed_names(tools: Sequence[ToolSpec]) -> None:
        seen: dict[str, str] = {}
        for tool in tools:
            name = tool.exposed_name.lower()
            previous = seen.get(name)
            if previous is not None and previous != tool.key:
                raise ValueError(
                    "duplicate model-visible tool name "
                    f"{tool.exposed_name!r}; assign distinct ToolSpec.model_name aliases"
                )
            seen[name] = tool.key

    async def _route_raw(self, text: str, tools: Sequence[ToolSpec]):
        self._validate_exposed_names(tools)
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
        names: list[str] = []
        for item in getattr(routing, "selected", ()):
            if isinstance(item, Mapping):
                fn = item.get("function") if isinstance(item.get("function"), Mapping) else item
                name = fn.get("name")
                if name:
                    names.append(str(name).lower())
        index = {tool.exposed_name.lower(): tool for tool in tools}
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
                    excluded_names={tool.exposed_name.lower() for tool in source},
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
            "selected_tools": [
                {"key": tool.key, "name": tool.name, "model_name": tool.exposed_name}
                for tool in selected
            ],
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
                        "name": call.model_name or call.name,
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
            "name": result.model_name or result.name,
            "content": result.model_content(),
        }

    @staticmethod
    def _resolve_call(call: ToolCall, visible: Sequence[ToolSpec]) -> tuple[ToolCall, ToolSpec | None]:
        index = {tool.exposed_name.lower(): tool for tool in visible}
        tool = index.get(call.name.lower())
        if tool is None:
            return call, None
        return (
            ToolCall(
                id=call.id,
                name=tool.name,
                model_name=call.name,
                tool_key=tool.key,
                arguments=dict(call.arguments),
                provider=tool.provider,
                parse_error=call.parse_error,
                raw=call.raw,
            ),
            tool,
        )

    @staticmethod
    def _validate_arguments(call: ToolCall, tool: ToolSpec) -> str | None:
        if call.parse_error:
            return call.parse_error
        schema = dict(tool.input_schema or {})
        if not schema:
            return None
        try:
            validator = Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(dict(call.arguments)), key=lambda item: list(item.path))
        except SchemaError as exc:
            return f"invalid-tool-schema:{exc.message}"
        if not errors:
            return None
        error = errors[0]
        path = ".".join(str(part) for part in error.path)
        suffix = f" at {path}" if path else ""
        return f"schema-validation:{error.message}{suffix}"

    async def _authorize(
        self,
        call: ToolCall,
        tool: ToolSpec,
        ctx: RunContext,
        visible: Sequence[ToolSpec],
    ) -> AuthorizationDecision:
        visible_names = {item.exposed_name for item in visible}
        tool_risks = {item.exposed_name: item.risk_level for item in visible}
        auth_context: dict[str, object] = {
            "identity": ctx.identity,
            "role": ctx.role,
            "tenant": ctx.tenant,
            "permissions": ctx.permissions,
            "scopes": ctx.scopes,
            "environment": ctx.environment,
            "risk_tier": ctx.risk_tier,
            "human_approved": ctx.human_approved,
            "model_visible_tools": visible_names,
            "tool_risks": tool_risks,
            "tool_arguments": dict(call.arguments),
            "tool_key": tool.key,
            "tool_name": tool.name,
            "tool_model_name": tool.exposed_name,
            "tool_provider": tool.provider,
            "tool_source": tool.source,
            "tool_metadata": dict(tool.metadata),
            "metadata": ctx.metadata,
        }
        rich_hook = getattr(self.authorization_policy, "authorize_tool", None)
        if rich_hook is not None:
            try:
                decision = rich_hook(call=call, tool=tool, context=auth_context)
                if inspect.isawaitable(decision):
                    decision = await decision
            except Exception as exc:
                return AuthorizationDecision(False, "policy_error", type(exc).__name__)
            if not isinstance(decision, AuthorizationDecision):
                return AuthorizationDecision(False, "invalid_policy_decision")
            return decision
        return AuthorizationGate(self.authorization_policy).authorize(
            action=tool.exposed_name,
            context=auth_context,
        )

    @staticmethod
    def _finish(
        *,
        status: str,
        response: ModelResponse | None,
        tool_results: Sequence[ToolResult],
        preview: RoutingPreview | None,
        recovery_attempts: int,
        provenance: Mapping[str, Any],
        telemetry: RuntimeTelemetry,
    ) -> RuntimeResult:
        return RuntimeResult(
            status=status,
            response=response,
            tool_results=tuple(tool_results),
            selected_tools=tuple(tool.name for tool in (preview.selected if preview else ())),
            routing_confidence=preview.confidence if preview else None,
            routing_abstained=preview.abstained if preview else False,
            recovery_attempts=recovery_attempts,
            provenance=provenance,
            telemetry=telemetry.as_dict(),
        )

    async def run(
        self,
        text: str,
        *,
        context: RunContext | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        model_kwargs: Mapping[str, Any] | None = None,
    ) -> RuntimeResult:
        ctx = context or RunContext()
        telemetry = RuntimeTelemetry()

        started = time.perf_counter()
        source = self._dedupe(list(await self.catalog.list_tools(ctx)))
        telemetry.record(
            "catalog",
            (time.perf_counter() - started) * 1000.0,
            metadata={"catalog_size": len(source)},
        )

        active_source = list(source)
        conversation = list(messages or [{"role": "user", "content": text}])
        tool_results: list[ToolResult] = []
        recovery_attempts = 0
        last_response: ModelResponse | None = None
        last_preview: RoutingPreview | None = None
        failed_keys: set[str] = set()
        run_provenance: dict[str, Any] = {"turns": []}

        for turn in range(self.max_model_turns):
            candidates = [tool for tool in active_source if tool.key not in failed_keys]

            started = time.perf_counter()
            preview = await self.preview_route(text, context=ctx, tools=candidates)
            telemetry.record(
                "scope_route",
                (time.perf_counter() - started) * 1000.0,
                metadata={
                    "turn": turn + 1,
                    "candidate_count": len(candidates),
                    "selected_count": len(preview.selected),
                    "confidence": preview.confidence,
                    "abstained": preview.abstained,
                },
            )
            last_preview = preview

            started = time.perf_counter()
            response = await self._complete(conversation, preview.selected, model_kwargs)
            telemetry.record(
                "model",
                (time.perf_counter() - started) * 1000.0,
                metadata={
                    "turn": turn + 1,
                    "tool_call_count": len(response.tool_calls),
                    "finish_reason": response.finish_reason,
                    "usage": dict(response.usage),
                },
            )
            last_response = response
            run_provenance["turns"].append(
                {
                    "turn": turn + 1,
                    "selected_tools": [
                        {"key": tool.key, "name": tool.name, "model_name": tool.exposed_name}
                        for tool in preview.selected
                    ],
                    "routing_confidence": preview.confidence,
                    "routing_abstained": preview.abstained,
                    "routing": dict(preview.provenance),
                    "tool_calls": [call.name for call in response.tool_calls],
                }
            )

            if not response.tool_calls:
                return self._finish(
                    status="completed",
                    response=response,
                    tool_results=tool_results,
                    preview=preview,
                    recovery_attempts=recovery_attempts,
                    provenance=run_provenance,
                    telemetry=telemetry,
                )

            conversation.append(self._assistant_tool_message(response))
            turn_failed = False

            for raw_call in response.tool_calls:
                call, tool = self._resolve_call(raw_call, preview.selected)
                if tool is None:
                    result = ToolResult(
                        tool_call_id=raw_call.id,
                        name=raw_call.name,
                        model_name=raw_call.name,
                        success=False,
                        error="authorization-denied:tool-not-model-visible",
                    )
                    telemetry.record(
                        "authorization",
                        0.0,
                        outcome="denied",
                        metadata={"model_name": raw_call.name, "reason": "tool-not-model-visible"},
                    )
                    tool_results.append(result)
                    conversation.append(self._tool_message(result))
                    turn_failed = True
                    continue

                started = time.perf_counter()
                validation_error = self._validate_arguments(call, tool)
                telemetry.record(
                    "schema_validation",
                    (time.perf_counter() - started) * 1000.0,
                    outcome="denied" if validation_error else "ok",
                    metadata={"tool_key": tool.key, "tool": tool.name},
                )
                if validation_error:
                    result = ToolResult(
                        tool_call_id=call.id,
                        name=tool.name,
                        model_name=tool.exposed_name,
                        tool_key=tool.key,
                        success=False,
                        error=f"invalid-tool-arguments:{validation_error}",
                    )
                    tool_results.append(result)
                    conversation.append(self._tool_message(result))
                    turn_failed = True
                    continue

                started = time.perf_counter()
                decision = await self._authorize(call, tool, ctx, preview.selected)
                telemetry.record(
                    "authorization",
                    (time.perf_counter() - started) * 1000.0,
                    outcome="ok" if decision.allowed else "denied",
                    metadata={
                        "tool_key": tool.key,
                        "tool": tool.name,
                        "reason": decision.reason_code,
                    },
                )
                if not decision.allowed:
                    result = ToolResult(
                        tool_call_id=call.id,
                        name=tool.name,
                        model_name=tool.exposed_name,
                        tool_key=tool.key,
                        success=False,
                        error=f"authorization-denied:{decision.reason_code}",
                    )
                    failed_keys.add(tool.key)
                else:
                    started = time.perf_counter()
                    result = await self.executor.execute(call, ctx)
                    telemetry.record(
                        "execution",
                        (time.perf_counter() - started) * 1000.0,
                        outcome="ok" if result.success else "error",
                        metadata={"tool_key": tool.key, "tool": tool.name},
                    )
                    if result.model_name is None or result.tool_key is None:
                        result = ToolResult(
                            tool_call_id=result.tool_call_id,
                            name=result.name,
                            success=result.success,
                            content=result.content,
                            structured_content=result.structured_content,
                            error=result.error,
                            model_name=result.model_name or tool.exposed_name,
                            tool_key=result.tool_key or tool.key,
                            metadata=result.metadata,
                            raw=result.raw,
                        )
                    if not result.success:
                        failed_keys.add(tool.key)

                tool_results.append(result)
                conversation.append(self._tool_message(result))
                if not result.success:
                    turn_failed = True

            if turn_failed:
                recovery_attempts += 1
                telemetry.record(
                    "recovery",
                    0.0,
                    outcome="retry" if recovery_attempts <= self.max_recovery_attempts else "exhausted",
                    metadata={"attempt": recovery_attempts},
                )
                if recovery_attempts > self.max_recovery_attempts:
                    return self._finish(
                        status="needs-review",
                        response=response,
                        tool_results=tool_results,
                        preview=preview,
                        recovery_attempts=recovery_attempts,
                        provenance=run_provenance,
                        telemetry=telemetry,
                    )
                continue

        return self._finish(
            status="max-turns",
            response=last_response,
            tool_results=tool_results,
            preview=last_preview,
            recovery_attempts=recovery_attempts,
            provenance=run_provenance,
            telemetry=telemetry,
        )
