from types import SimpleNamespace

import pytest

from agentweave import (
    AgentWeaveApplication,
    AgentWeaveRuntime,
    ComponentRegistry,
    PluginManager,
    RunContext,
    StaticToolCatalog,
    ToolResult,
    ToolSpec,
)
from agentweave.integrations.mcp import MCPConnection, MCPExecutor, MCPToolCatalog
from agentweave_byom import ToolRoutingResult
from agentweave_security.authorization import AuthorizationDecision


class FirstRouter:
    version = "test-first"

    async def aroute(self, text, tools, *, max_tools=8):
        return ToolRoutingResult(
            selected=list(tools[:max_tools]),
            filtered=list(tools[max_tools:]),
            provenance={"router": self.version},
            confidence=1.0,
            abstained=False,
        )


class ToolCallingModel:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

    async def complete(self, messages, *, tools=None, **kwargs):
        if messages and messages[-1].get("role") == "tool":
            return {"choices": [{"message": {"content": "done", "tool_calls": []}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": self.name,
                                    "arguments": self.arguments,
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


class CountingExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, call, context):
        self.calls.append(call)
        return ToolResult(
            call.id,
            call.name,
            True,
            content="ok",
            model_name=call.model_name,
            tool_key=call.tool_key,
        )


@pytest.mark.asyncio
async def test_duplicate_runtime_ids_are_not_silently_collapsed_and_aliases_disambiguate():
    tools = [
        ToolSpec(id="mcp:a:search", name="search", model_name="a_search", provider="mcp", source="a"),
        ToolSpec(id="mcp:b:search", name="search", model_name="b_search", provider="mcp", source="b"),
    ]
    runtime = AgentWeaveRuntime(
        model=ToolCallingModel("a_search", "{}"),
        catalog=StaticToolCatalog(tools),
        executor=CountingExecutor(),
        router=FirstRouter(),
        max_tools=2,
    )
    preview = await runtime.preview_route("search")
    assert {tool.key for tool in preview.selected} == {"mcp:a:search", "mcp:b:search"}


@pytest.mark.asyncio
async def test_duplicate_model_names_fail_loudly_instead_of_collapsing():
    runtime = AgentWeaveRuntime(
        model=ToolCallingModel("search", "{}"),
        catalog=StaticToolCatalog(
            [
                ToolSpec(id="a", name="search", provider="mcp", source="a"),
                ToolSpec(id="b", name="search", provider="mcp", source="b"),
            ]
        ),
        executor=CountingExecutor(),
        router=FirstRouter(),
        max_tools=2,
    )
    with pytest.raises(ValueError, match="duplicate model-visible tool name"):
        await runtime.preview_route("search")


@pytest.mark.asyncio
async def test_invalid_arguments_fail_before_authorization_or_execution():
    executor = CountingExecutor()
    runtime = AgentWeaveRuntime(
        model=ToolCallingModel("pay", '{"amount":"not-a-number"}'),
        catalog=StaticToolCatalog(
            [
                ToolSpec(
                    name="pay",
                    input_schema={
                        "type": "object",
                        "properties": {"amount": {"type": "number"}},
                        "required": ["amount"],
                        "additionalProperties": False,
                    },
                )
            ]
        ),
        executor=executor,
        router=FirstRouter(),
        max_recovery_attempts=0,
    )
    result = await runtime.run("pay")
    assert result.status == "needs-review"
    assert executor.calls == []
    assert result.tool_results[0].error.startswith("invalid-tool-arguments:schema-validation")


@pytest.mark.asyncio
async def test_argument_aware_authorization_receives_resolved_tool_and_arguments():
    class AmountPolicy:
        def authorize_tool(self, *, call, tool, context):
            assert tool.key == "payments:prod:transfer"
            assert context["tool_arguments"] == {"amount": 250}
            if call.arguments["amount"] > 100:
                return AuthorizationDecision(False, "amount-limit")
            return AuthorizationDecision(True, "allowed")

    executor = CountingExecutor()
    runtime = AgentWeaveRuntime(
        model=ToolCallingModel("transfer", '{"amount":250}'),
        catalog=StaticToolCatalog(
            [
                ToolSpec(
                    id="payments:prod:transfer",
                    name="transfer",
                    provider="payments",
                    source="prod",
                    input_schema={
                        "type": "object",
                        "properties": {"amount": {"type": "number"}},
                        "required": ["amount"],
                    },
                )
            ]
        ),
        executor=executor,
        router=FirstRouter(),
        authorization_policy=AmountPolicy(),
        max_recovery_attempts=0,
    )
    result = await runtime.run("transfer 250")
    assert executor.calls == []
    assert result.tool_results[0].error == "authorization-denied:amount-limit"


@pytest.mark.asyncio
async def test_runtime_reports_stage_telemetry():
    runtime = AgentWeaveRuntime(
        model=ToolCallingModel("read", "{}"),
        catalog=StaticToolCatalog([ToolSpec(name="read")]),
        executor=CountingExecutor(),
        router=FirstRouter(),
    )
    result = await runtime.run("read")
    stages = [event["stage"] for event in result.telemetry["events"]]
    assert "catalog" in stages
    assert "scope_route" in stages
    assert "model" in stages
    assert "schema_validation" in stages
    assert "authorization" in stages
    assert "execution" in stages


class FakeMCPClient:
    async def list_tools(self, cursor=None):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="read",
                    description="read",
                    input_schema={"type": "object", "properties": {}},
                    output_schema=None,
                    annotations=SimpleNamespace(destructiveHint=False),
                    meta={"agentweave": {"permissions": ["read"]}},
                    title="Read",
                )
            ],
            next_cursor=None,
        )

    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            is_error=False,
            content=[{"type": "text", "text": "ok"}],
            structured_content={"ok": True},
            meta={},
        )


class CountingCM:
    def __init__(self, client, counters):
        self.client = client
        self.counters = counters

    async def __aenter__(self):
        self.counters["enter"] += 1
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        self.counters["exit"] += 1
        return False


@pytest.mark.asyncio
async def test_shared_mcp_connection_is_reused_and_policy_metadata_is_restrictive():
    client = FakeMCPClient()
    counters = {"enter": 0, "exit": 0}
    connection = MCPConnection(
        object(),
        client_factory=lambda target: CountingCM(client, counters),
    )
    catalog = MCPToolCatalog(connection=connection, source="unit")
    executor = MCPExecutor(connection=connection)

    await connection.start()
    tools = await catalog.list_tools(RunContext(permissions=frozenset({"read"})))
    result = await executor.execute(
        SimpleNamespace(id="c1", name="read", arguments={}, model_name="read", tool_key=tools[0].key),
        RunContext(),
    )
    await connection.stop()

    assert counters == {"enter": 1, "exit": 1}
    assert tools[0].permissions == frozenset({"read"})
    assert result.success is True


class FinalModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}


class LifecyclePlugin:
    name = "life"
    api_version = "1"

    def __init__(self):
        self.events = []

    def configure(self, registry: ComponentRegistry):
        self.events.append("configure")

    async def start(self, runtime):
        self.events.append("start")

    async def stop(self):
        self.events.append("stop")


@pytest.mark.asyncio
async def test_application_owns_plugin_and_runtime_lifecycle():
    plugin = LifecyclePlugin()
    manager = PluginManager()
    manager.register("life", plugin)
    runtime = AgentWeaveRuntime(
        model=FinalModel(),
        catalog=StaticToolCatalog([]),
        executor=CountingExecutor(),
        router=FirstRouter(),
    )
    app = AgentWeaveApplication(runtime, plugin_manager=manager)
    result = await app.run("hello")
    assert result.status == "completed"
    assert plugin.events == ["configure", "start", "stop"]
    assert runtime._started is False
