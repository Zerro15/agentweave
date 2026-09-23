from __future__ import annotations

import inspect
from typing import Any, Mapping, Sequence

from agentweave.orchestrator import AgentWeave

from .model_adapters import ModelAdapter
from .tool_routing import AdaptiveRouter, AsyncRouter, DeterministicRouterV1, Router


class BYOMAgentWeave(AgentWeave):
    """AgentWeave with a user-supplied model and tool catalog.

    The default BYOM path is confidence-aware and now supports both synchronous and
    asynchronous routers/search providers without changing model-adapter code.
    """

    def __init__(
        self,
        *args: Any,
        model: ModelAdapter | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_router: Router | AsyncRouter | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.model = model
        self.tools = list(tools or [])
        self.tool_router = tool_router or AdaptiveRouter(
            DeterministicRouterV1(self.analyzer)
        )

    def set_model(self, model: ModelAdapter) -> "BYOMAgentWeave":
        self.model = model
        return self

    def set_tools(self, tools: Sequence[Mapping[str, Any]]) -> "BYOMAgentWeave":
        self.tools = list(tools)
        return self

    def set_router(self, router: Router | AsyncRouter) -> "BYOMAgentWeave":
        self.tool_router = router
        return self

    def route_tools(
        self,
        text: str,
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        max_tools: int = 8,
    ):
        method = getattr(self.tool_router, "route", None)
        if method is None:
            raise TypeError("async-only router requires await route_tools_async(...)")
        result = method(
            text,
            self.tools if tools is None else tools,
            max_tools=max_tools,
        )
        if inspect.isawaitable(result):
            raise TypeError("async router requires await route_tools_async(...)")
        return result

    async def route_tools_async(
        self,
        text: str,
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        max_tools: int = 8,
    ):
        method = getattr(self.tool_router, "aroute", None) or getattr(
            self.tool_router, "route", None
        )
        if method is None:
            raise TypeError("router must define route() or aroute()")
        result = method(
            text,
            self.tools if tools is None else tools,
            max_tools=max_tools,
        )
        if inspect.isawaitable(result):
            result = await result
        return result

    async def run(
        self,
        text: str,
        *,
        model: ModelAdapter | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        max_tools: int = 8,
        messages: Sequence[Mapping[str, Any]] | None = None,
        model_kwargs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_model = model or self.model
        if active_model is None:
            raise ValueError("No model configured. Pass model=... or call set_model(...).")

        active_tools = self.tools if tools is None else list(tools)
        routing = await self.route_tools_async(
            text,
            tools=active_tools,
            max_tools=max_tools,
        )
        model_messages = list(messages or [{"role": "user", "content": text}])

        with self.observability.tracer.span(
            "agentweave.model.invoke",
            model=active_model.identity,
            tools_before=len(active_tools),
            tools_after=len(routing.selected),
            routing_confidence=routing.confidence,
            routing_abstained=routing.abstained,
        ):
            response = await active_model.complete(
                model_messages,
                tools=routing.selected,
                **dict(model_kwargs or {}),
            )

        provenance = dict(routing.provenance)
        provenance["model_adapter"] = active_model.identity
        self.observability.audit.record("tool-routing.completed", payload=provenance)
        self.observability.metrics.inc("model_invocations_total", model=active_model.identity)
        if routing.abstained:
            self.observability.metrics.inc("routing_abstentions_total")

        return {
            "status": "completed",
            "model": active_model.identity,
            "response": response,
            "selected_tools": routing.selected,
            "filtered_tools": routing.filtered,
            "routing_confidence": routing.confidence,
            "routing_abstained": routing.abstained,
            "routing_provenance": provenance,
            "observability": self.observability.snapshot(),
        }
