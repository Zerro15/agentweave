import pytest


def test_mcp_upstream_sdk_surface():
    mcp = pytest.importorskip("mcp")
    client = getattr(mcp, "Client", None)
    assert client is not None, "mcp>=2 must expose mcp.Client"
    from agentweave.integrations.mcp import MCPConnection, MCPExecutor, MCPToolCatalog

    assert MCPConnection is not None
    assert MCPToolCatalog is not None
    assert MCPExecutor is not None


def test_langgraph_upstream_surface_and_adapter_import():
    graph = pytest.importorskip("langgraph.graph")
    assert getattr(graph, "StateGraph", None) is not None
    from agentweave.integrations.langgraph import AgentWeaveLangGraphNode, langgraph_node

    assert AgentWeaveLangGraphNode is not None
    assert callable(langgraph_node)


def test_autogen_upstream_surface_and_adapter_import():
    teams = pytest.importorskip("autogen_agentchat.teams")
    assert getattr(teams, "SelectorGroupChat", None) is not None
    from agentweave.integrations.autogen import AgentWeaveAutoGenSelector

    assert AgentWeaveAutoGenSelector is not None
