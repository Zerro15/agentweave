import pytest
from agentweave.discovery import AgentCardDiscovery


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "name": "Hello World Agent",
            "version": "1.0.0",
            "capabilities": {"streaming": True},
            "supportedInterfaces": [
                {
                    "url": "http://127.0.0.1:9999/",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "skills": [{"id": "echo_bot", "name": "Echo Bot"}],
        }


class FakeTransport:
    async def get(self, url, **kwargs):
        return FakeResponse()


@pytest.mark.asyncio
async def test_discovery_parses_a2a_v1_supported_interfaces():
    agent = await AgentCardDiscovery(transport=FakeTransport()).fetch(
        "http://host/.well-known/agent-card.json"
    )
    assert agent.execution.endpoint == "http://127.0.0.1:9999/"
    assert agent.metadata["protocol_binding"] == "JSONRPC"
    assert agent.metadata["protocol_version"] == "1.0"
    assert agent.metadata["streaming"] is True
    assert agent.capabilities[0].name == "echo_bot"
