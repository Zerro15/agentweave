from __future__ import annotations

from typing import Any, Callable, Mapping

from ..runtime import AgentWeaveRuntime
from ..runtime_types import RunContext


class AgentWeaveLangGraphNode:
    """Async LangGraph-compatible routing node backed only by the public runtime API."""

    def __init__(
        self,
        runtime: AgentWeaveRuntime,
        *,
        query_key: str = "query",
        context_factory: Callable[[Mapping[str, Any]], RunContext] | None = None,
    ) -> None:
        self.runtime = runtime
        self.query_key = query_key
        self.context_factory = context_factory

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        query = str(state[self.query_key])
        context = self.context_factory(state) if self.context_factory else RunContext()
        preview = await self.runtime.preview_route(query, context=context)
        return {
            "agentweave_selected_tools": [tool.name for tool in preview.selected],
            "agentweave_permitted_tools": [tool.name for tool in preview.permitted],
            "agentweave_routing_confidence": preview.confidence,
            "agentweave_routing_abstained": preview.abstained,
            "agentweave_routing_provenance": dict(preview.provenance),
        }


def langgraph_node(
    runtime: AgentWeaveRuntime,
    **kwargs: Any,
) -> AgentWeaveLangGraphNode:
    """Return an async callable suitable for ``StateGraph.add_node``."""
    return AgentWeaveLangGraphNode(runtime, **kwargs)
