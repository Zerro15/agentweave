import pytest

from agentweave import (
    ComponentRegistry,
    PluginManager,
    RuntimeConfig,
    RuntimeFactory,
    ToolResult,
)


def test_runtime_config_is_typed_and_rejects_missing_required_fields():
    cfg = RuntimeConfig.from_dict(
        {
            "model": {
                "kind": "openai-compatible",
                "model": "demo",
                "base_url": "https://model.example/v1",
                "api_key_env": "MODEL_KEY",
            },
            "catalog": {"kind": "mcp", "target": "https://tools.example/mcp"},
            "routing": {"kind": "adaptive", "max_tools": 5},
            "limits": {"max_model_turns": 3, "max_recovery_attempts": 1},
        }
    )
    assert cfg.model.model == "demo"
    assert cfg.catalog.kind == "mcp"
    assert cfg.routing.max_tools == 5
    assert cfg.limits.max_recovery_attempts == 1

    with pytest.raises(ValueError, match="model.model"):
        RuntimeConfig.from_dict(
            {
                "model": {
                    "kind": "openai-compatible",
                    "base_url": "https://model.example/v1",
                },
                "catalog": {"kind": "mcp", "target": "x"},
            }
        )


class FakeModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}


class FakeCatalog:
    async def list_tools(self, context):
        return []


class FakeExecutor:
    async def execute(self, call, context):
        return ToolResult(call.id, call.name, True, content="ok")


def test_runtime_factory_can_use_plugin_registered_components():
    registry = ComponentRegistry()
    registry.register("models", "fake-model", lambda cfg: FakeModel())
    registry.register("catalogs", "fake-tools", lambda cfg: FakeCatalog())
    registry.register("executors", "fake-tools", lambda cfg: FakeExecutor())

    cfg = RuntimeConfig.from_dict(
        {
            "model": {"kind": "fake-model", "model": "fake"},
            "catalog": {"kind": "fake-tools", "target": "local"},
            "routing": {"kind": "deterministic"},
        }
    )
    runtime = RuntimeFactory(registry=registry).build(cfg)
    assert isinstance(runtime.model, FakeModel)
    assert isinstance(runtime.catalog, FakeCatalog)
    assert isinstance(runtime.executor, FakeExecutor)


class DemoPlugin:
    name = "demo"
    api_version = "1.0"

    def __init__(self):
        self.events = []

    def configure(self, registry):
        self.events.append("configure")
        registry.register("routers", "demo", object())

    async def start(self, runtime):
        self.events.append("start")

    async def stop(self):
        self.events.append("stop")


@pytest.mark.asyncio
async def test_plugin_lifecycle_is_versioned_and_idempotent():
    plugin = DemoPlugin()
    manager = PluginManager()
    manager.register("demo", plugin)
    manager.configure()
    manager.configure()
    await manager.start(object())
    await manager.start(object())
    await manager.stop()

    assert plugin.events == ["configure", "start", "stop"]
    assert "demo" in manager.registry.routers


def test_plugin_manager_rejects_incompatible_api_version():
    class BadPlugin(DemoPlugin):
        api_version = "2"

    with pytest.raises(RuntimeError, match="targets API 2"):
        PluginManager().register("bad", BadPlugin())
