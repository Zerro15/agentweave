import pytest

from agentweave.a2a import HttpA2AAdapter
from agentweave.models import AgentProfile, Capability, ExecutionProfile
from agentweave.validation import SecurityValidator


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def build_request(self, method, url, **kwargs):
        return {"method": method, "url": url, **kwargs}

    async def send(self, request, *, follow_redirects=False):
        payload = request.get("json") or {}
        headers = request.get("headers") or {}
        self.calls.append((request["url"], payload, headers))
        method = payload.get("method")
        message = (payload.get("params") or {}).get("message") or {}
        if method == "message/send" and message.get("role") == "user":
            return FakeResponse({"error": {"code": -32602, "message": "legacy role required"}})
        if method == "message/send" and message.get("role") == "ROLE_USER":
            return FakeResponse({"result": {"ok": True}})
        if method == "SendMessage":
            return FakeResponse({"result": {"ok": True}})
        return FakeResponse({"error": {"code": -32601, "message": "Method not found"}})


def _agent():
    return AgentProfile(
        "x",
        "X",
        [Capability("echo")],
        execution=ExecutionProfile(endpoint="https://example.test/a2a"),
        metadata={
            "protocol_binding": "JSONRPC",
            "protocol_version": "1.0",
            "agent_card": {},
        },
    )


def _adapter():
    validator = SecurityValidator(resolver=lambda host: {"93.184.216.34"})
    return HttpA2AAdapter(endpoint_validator=validator)


@pytest.mark.asyncio
async def test_jsonrpc_adapts_current_method_to_legacy_message_shape(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr("agentweave.a2a.httpx.AsyncClient", lambda **kwargs: FakeClient())
    out = await _adapter().invoke(_agent(), "hello")
    assert out["ok"] is True
    assert [c[1]["method"] for c in FakeClient.calls][:2] == ["message/send", "message/send"]
    assert FakeClient.calls[1][1]["params"]["message"]["role"] == "ROLE_USER"
    assert "93.184.216.34" in FakeClient.calls[0][0]
    assert FakeClient.calls[0][2]["Host"] == "example.test"


@pytest.mark.asyncio
async def test_explicit_structured_message_and_method(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr("agentweave.a2a.httpx.AsyncClient", lambda **kwargs: FakeClient())
    message = {
        "role": "ROLE_USER",
        "messageId": "m1",
        "parts": [
            {
                "mediaType": "application/json",
                "data": {
                    "skill": "search_public_research",
                    "query": "agent interoperability",
                    "limit": 2,
                },
            }
        ],
    }
    out = await _adapter().invoke_message(_agent(), message, rpc_method="SendMessage")
    assert out["ok"] is True
    assert FakeClient.calls[0][1]["method"] == "SendMessage"
    assert FakeClient.calls[0][1]["params"]["message"] == message
