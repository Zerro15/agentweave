import pytest

from agentweave import AgentWeaveRuntime, CallableExecutor, StaticToolCatalog, ToolSpec
from agentweave.integrations.autogen import AgentWeaveAutoGenSelector
from agentweave.integrations.langgraph import AgentWeaveLangGraphNode
from agentweave_byom import ToolRoutingResult


class FirstTwoRouter:
    version = "test-router"

    async def aroute(self, text, tools, *, max_tools=8):
        selected = list(tools[:max_tools])
        return ToolRoutingResult(
            selected=selected,
            filtered=list(tools[max_tools:]),
            provenance={"router": self.version},
            confidence=0.9,
            abstained=False,
        )


class UnusedModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        raise AssertionError("routing adapters must not invoke the model")


def runtime(tools=()):
    return AgentWeaveRuntime(
        model=UnusedModel(),
        catalog=StaticToolCatalog(list(tools)),
        executor=CallableExecutor({}),
        router=FirstTwoRouter(),
        max_tools=2,
    )


@pytest.mark.asyncio
async def test_langgraph_node_uses_public_runtime_preview():
    node = AgentWeaveLangGraphNode(
        runtime(
            [
                ToolSpec(name="research", description="research evidence"),
                ToolSpec(name="coding", description="write code"),
            ]
        )
    )
    update = await node({"query": "research this"})
    assert update["agentweave_selected_tools"] == ["research", "coding"]
    assert update["agentweave_routing_confidence"] == 0.9


@pytest.mark.asyncio
async def test_autogen_selector_uses_runtime_preview_without_internal_components():
    selector = AgentWeaveAutoGenSelector(
        runtime(),
        {
            "backend": "backend API specialist",
            "database": "database and SQL specialist",
            "research": "research specialist",
        },
        max_participants=2,
    )
    selected = await selector.select("review backend and database")
    assert selected == ["backend", "database"]
