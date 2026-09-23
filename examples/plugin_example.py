"""Minimal typed AgentWeave plugin example.

A separately packaged plugin can expose ``ExamplePlugin`` under the
``agentweave.plugins`` Python entry-point group.
"""

from agentweave import ComponentRegistry


class ExamplePlugin:
    name = "example"
    api_version = "1.0"

    def configure(self, registry: ComponentRegistry) -> None:
        registry.register("routers", "example-router", object())

    async def start(self, runtime) -> None:
        return None

    async def stop(self) -> None:
        return None
