from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agentweave import AgentWeaveRuntime, CallableExecutor, StaticToolCatalog, ToolSpec
from agentweave.integrations.langgraph import langgraph_node


class RoutingState(TypedDict, total=False):
    query: str
    agentweave_selected_tools: list[str]
    agentweave_permitted_tools: list[str]
    agentweave_routing_confidence: float
    agentweave_routing_abstained: bool
    agentweave_routing_provenance: dict
    result: str


class _UnusedModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        raise RuntimeError("This routing-only example never invokes the model")


def build_runtime() -> AgentWeaveRuntime:
    specialists = [
        ToolSpec(
            name="research_specialist",
            description="Researches evidence, sources, and technical background",
            provider="langgraph",
        ),
        ToolSpec(
            name="coding_specialist",
            description="Reviews code, APIs, and implementation details",
            provider="langgraph",
        ),
        ToolSpec(
            name="policy_specialist",
            description="Reviews policy, compliance, risk, and governance",
            provider="langgraph",
        ),
    ]
    return AgentWeaveRuntime(
        model=_UnusedModel(),
        catalog=StaticToolCatalog(specialists),
        executor=CallableExecutor({}),
        max_tools=2,
    )


RUNTIME = build_runtime()


async def downstream_work(state: RoutingState) -> dict[str, Any]:
    selected = state.get("agentweave_selected_tools", [])
    return {
        "result": (
            "LangGraph would continue with: " + ", ".join(selected)
            if selected
            else "No suitable AgentWeave route was found."
        )
    }


def build_graph():
    graph = StateGraph(RoutingState)
    graph.add_node("agentweave_route", langgraph_node(RUNTIME))
    graph.add_node("downstream_work", downstream_work)
    graph.add_edge(START, "agentweave_route")
    graph.add_edge("agentweave_route", "downstream_work")
    graph.add_edge("downstream_work", END)
    return graph.compile()


async def main() -> None:
    app = build_graph()
    output = await app.ainvoke(
        {"query": "Analyze this policy and recommend a compliant implementation plan"}
    )
    print("Selected specialists:", output["agentweave_selected_tools"])
    print("Routing confidence:", output["agentweave_routing_confidence"])
    print(output["result"])


if __name__ == "__main__":
    asyncio.run(main())
