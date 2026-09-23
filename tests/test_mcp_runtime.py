from types import SimpleNamespace

import pytest

from agentweave import RunContext, ToolCall
from agentweave.integrations.mcp import MCPExecutor, MCPToolCatalog


class FakeClient:
    def __init__(self):
        self.list_calls = []
        self.call_calls = []

    async def list_tools(self, cursor=None):
        self.list_calls.append(cursor)
        if cursor is None:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="search",
                        title="Search",
                        description="Search records",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        output_schema=None,
                        annotations=None,
                        meta={"source": "fake"},
                    )
                ],
                next_cursor="next",
            )
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="write",
                    title="Write",
                    description="Write a record",
                    input_schema={"type": "object", "properties": {}},
                    output_schema=None,
                    annotations=None,
                    meta=None,
                )
            ],
            next_cursor=None,
        )

    async def call_tool(self, name, arguments):
        self.call_calls.append((name, dict(arguments)))
        return SimpleNamespace(
            is_error=False,
            content=[{"type": "text", "text": "ok"}],
            structured_content={"ok": True},
            meta={"server": "fake"},
        )


class FakeCM:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_mcp_catalog_uses_real_client_contract_and_paginates():
    client = FakeClient()
    factory = lambda target: FakeCM(client)
    catalog = MCPToolCatalog(
        object(),
        client_factory=factory,
        source="unit-test",
    )
    tools = await catalog.list_tools(RunContext())

    assert [tool.name for tool in tools] == ["search", "write"]
    assert tools[0].provider == "mcp"
    assert tools[0].source == "unit-test"
    assert tools[0].input_schema["type"] == "object"
    assert client.list_calls == [None, "next"]


@pytest.mark.asyncio
async def test_mcp_executor_normalizes_call_tool_result():
    client = FakeClient()
    executor = MCPExecutor(
        object(),
        client_factory=lambda target: FakeCM(client),
    )
    result = await executor.execute(
        ToolCall(id="call-1", name="search", arguments={"query": "agentweave"}),
        RunContext(),
    )

    assert result.success is True
    assert result.name == "search"
    assert result.structured_content == {"ok": True}
    assert result.metadata["server"] == "fake"
    assert client.call_calls == [("search", {"query": "agentweave"})]
