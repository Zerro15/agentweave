import pytest

from agentweave import (
    AgentWeaveRuntime,
    CallableExecutor,
    RunContext,
    StaticToolCatalog,
    ToolSpec,
)
from agentweave_byom import ToolRoutingResult


class FirstRouter:
    version = "test-first-router"

    async def aroute(self, text, tools, *, max_tools=8):
        selected = list(tools[:max_tools])
        return ToolRoutingResult(
            selected=selected,
            filtered=list(tools[max_tools:]),
            provenance={"router": self.version},
            confidence=1.0,
            abstained=False,
        )


class AbstainingRouter:
    version = "test-abstaining-router"

    async def aroute(self, text, tools, *, max_tools=8):
        selected = list(tools[:max_tools])
        return ToolRoutingResult(
            selected=selected,
            filtered=list(tools[max_tools:]),
            provenance={"router": self.version},
            confidence=0.1,
            abstained=True,
        )


class ToolThenFinalModel:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": list(tools or [])})
        if messages and messages[-1].get("role") == "tool":
            return {
                "choices": [
                    {
                        "message": {"content": "done", "tool_calls": []},
                        "finish_reason": "stop",
                    }
                ]
            }
        name = tools[0]["function"]["name"]
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": name, "arguments": '{"value": 7}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


@pytest.mark.asyncio
async def test_runtime_enforces_scope_route_authorize_execute_order():
    tools = [
        ToolSpec(
            name="read_record",
            description="read a customer record",
            permissions=frozenset({"read"}),
        ),
        ToolSpec(
            name="admin_delete",
            description="delete a customer record",
            permissions=frozenset({"admin"}),
            risk_level="high",
        ),
    ]
    model = ToolThenFinalModel()
    executed = []

    def read_record(value):
        executed.append(value)
        return {"value": value}

    runtime = AgentWeaveRuntime(
        model=model,
        catalog=StaticToolCatalog(tools),
        executor=CallableExecutor({"read_record": read_record}),
        router=FirstRouter(),
        max_tools=2,
    )
    result = await runtime.run(
        "read the record",
        context=RunContext(permissions=frozenset({"read"})),
    )

    assert result.status == "completed"
    assert executed == [7]
    assert [item.name for item in result.tool_results] == ["read_record"]
    first_turn = result.provenance["turns"][0]
    assert first_turn["selected_tools"] == [
        {
            "key": "tool:local:read_record",
            "name": "read_record",
            "model_name": "read_record",
        }
    ]
    decisions = first_turn["routing"]["scope"]["decisions"]
    assert any(
        item["tool"] == "admin_delete"
        and item["allowed"] is False
        and item["reason_code"] == "missing_permission"
        for item in decisions
    )


class SearchProvider:
    async def search(self, text, *, context, excluded_names, limit):
        return [
            ToolSpec(
                name="discovered_read",
                description="read discovered data",
                permissions=frozenset({"read"}),
            ),
            ToolSpec(
                name="discovered_admin",
                description="admin-only discovered action",
                permissions=frozenset({"admin"}),
            ),
        ]


@pytest.mark.asyncio
async def test_deferred_discovery_is_rescoped_before_model_exposure():
    runtime = AgentWeaveRuntime(
        model=ToolThenFinalModel(),
        catalog=StaticToolCatalog(
            [
                ToolSpec(
                    name="seed",
                    description="seed tool",
                    permissions=frozenset({"read"}),
                )
            ]
        ),
        executor=CallableExecutor({}),
        router=AbstainingRouter(),
        search_provider=SearchProvider(),
        max_tools=8,
    )

    preview = await runtime.preview_route(
        "ambiguous request",
        context=RunContext(permissions=frozenset({"read"})),
    )
    names = {tool.name for tool in preview.permitted}
    assert "discovered_read" in names
    assert "discovered_admin" not in names
    assert preview.provenance["deferred_search"]["discovered"] == 2
    assert preview.provenance["deferred_search"]["discovered_permitted"] == 1


class HallucinatingModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "bad-1",
                                "type": "function",
                                "function": {"name": "not_exposed", "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


class CountingExecutor:
    def __init__(self):
        self.calls = 0

    async def execute(self, call, context):
        self.calls += 1
        raise AssertionError("executor must not receive unauthorized calls")


@pytest.mark.asyncio
async def test_hallucinated_non_visible_tool_is_denied_before_executor():
    executor = CountingExecutor()
    runtime = AgentWeaveRuntime(
        model=HallucinatingModel(),
        catalog=StaticToolCatalog([ToolSpec(name="safe", description="safe")]),
        executor=executor,
        router=FirstRouter(),
        max_recovery_attempts=0,
    )
    result = await runtime.run("do something")
    assert result.status == "needs-review"
    assert executor.calls == 0
    assert result.tool_results[0].error == "authorization-denied:tool-not-model-visible"


class RecoveryModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        if messages and messages[-1].get("role") == "tool":
            last = messages[-1]["content"]
            if "RuntimeError" not in last:
                return {
                    "choices": [
                        {
                            "message": {"content": "recovered", "tool_calls": []},
                            "finish_reason": "stop",
                        }
                    ]
                }
        name = tools[0]["function"]["name"]
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call-{name}",
                                "type": "function",
                                "function": {"name": name, "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


@pytest.mark.asyncio
async def test_execution_failure_recovers_by_rerouting_without_failed_tool():
    def primary():
        raise RuntimeError("primary failed")

    def backup():
        return "ok"

    runtime = AgentWeaveRuntime(
        model=RecoveryModel(),
        catalog=StaticToolCatalog(
            [
                ToolSpec(name="primary", description="primary operation"),
                ToolSpec(name="backup", description="backup operation"),
            ]
        ),
        executor=CallableExecutor({"primary": primary, "backup": backup}),
        router=FirstRouter(),
        max_tools=1,
        max_model_turns=3,
        max_recovery_attempts=2,
    )
    result = await runtime.run("perform operation")
    assert result.status == "completed"
    assert [item.name for item in result.tool_results] == ["primary", "backup"]
    assert result.tool_results[0].success is False
    assert result.tool_results[1].success is True
    assert result.recovery_attempts == 1
