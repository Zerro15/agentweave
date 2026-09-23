import hashlib

import pytest

from agentweave import AgentProfile, AgentWeave, Capability
from agentweave.a2a import HttpA2AAdapter
from agentweave.validation import IdentityVerifier, SecurityValidator


class _Response:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_request(self, method, url, **kwargs):
        return (method, url, kwargs)

    async def send(self, request, *, follow_redirects=False):
        self.calls.append((request, follow_redirects))
        return self.responses.pop(0)


def test_signed_agent_card_requires_payload_binding(monkeypatch):
    verifier = IdentityVerifier()
    card = {"name": "agent", "url": "https://agent.example", "jws": "token"}

    monkeypatch.setattr(verifier, "verify_jws", lambda *args, **kwargs: {})
    with pytest.raises(ValueError, match="missing card_sha256"):
        verifier.verify_agent_card_jws(card, b"public-key")

    digest = hashlib.sha256(verifier.canonical_agent_card(card)).hexdigest()
    monkeypatch.setattr(
        verifier,
        "verify_jws",
        lambda *args, **kwargs: {"card_sha256": digest},
    )
    assert verifier.verify_agent_card_jws(card, b"public-key")["card_sha256"] == digest


@pytest.mark.asyncio
async def test_redirect_target_is_revalidated_and_private_redirect_is_blocked():
    def resolver(host):
        return {
            "public.example": {"93.184.216.34"},
            "metadata.internal": {"169.254.169.254"},
        }[host]

    validator = SecurityValidator(resolver=resolver)
    adapter = HttpA2AAdapter(endpoint_validator=validator, max_redirects=2)
    client = _Client([_Response(307, {"location": "https://metadata.internal/latest"})])

    with pytest.raises(ValueError, match="private-network-endpoint"):
        await adapter._request(client, "POST", "https://public.example/rpc", json={})
    assert len(client.calls) == 1
    request, follow_redirects = client.calls[0]
    _, pinned_url, request_kwargs = request
    assert "93.184.216.34" in pinned_url
    assert request_kwargs["headers"]["Host"] == "public.example"
    assert request_kwargs["extensions"]["sni_hostname"] == "public.example"
    assert follow_redirects is False


@pytest.mark.asyncio
async def test_cross_origin_redirect_does_not_forward_sensitive_headers():
    def resolver(host):
        return {
            "public.example": {"93.184.216.34"},
            "other.example": {"93.184.216.35"},
        }[host]

    validator = SecurityValidator(resolver=resolver)
    adapter = HttpA2AAdapter(endpoint_validator=validator, max_redirects=2)
    client = _Client(
        [
            _Response(307, {"location": "https://other.example/rpc"}),
            _Response(200),
        ]
    )

    await adapter._request(
        client,
        "POST",
        "https://public.example/rpc",
        headers={
            "Authorization": "Bearer top-secret",
            "Cookie": "session=top-secret",
            "Proxy-Authorization": "Basic top-secret",
            "X-Request-ID": "req-1",
        },
        json={},
    )

    assert len(client.calls) == 2
    first_request, _ = client.calls[0]
    second_request, _ = client.calls[1]
    first_headers = first_request[2]["headers"]
    second_headers = second_request[2]["headers"]
    assert first_headers["Authorization"] == "Bearer top-secret"
    assert first_headers["Cookie"] == "session=top-secret"
    assert first_headers["Proxy-Authorization"] == "Basic top-secret"
    assert not any(
        key.lower() in {"authorization", "cookie", "proxy-authorization"}
        for key in second_headers
    )
    assert second_headers["X-Request-ID"] == "req-1"
    assert second_headers["Host"] == "other.example"


@pytest.mark.asyncio
async def test_dns_rebinding_address_change_is_blocked_before_second_request():
    answers = iter([{"93.184.216.34"}, {"169.254.169.254"}])

    def resolver(host):
        assert host == "public.example"
        return next(answers)

    validator = SecurityValidator(resolver=resolver)
    adapter = HttpA2AAdapter(endpoint_validator=validator)
    snapshots = {}
    client = _Client([_Response(200), _Response(200)])

    await adapter._request(
        client,
        "POST",
        "https://public.example/rpc",
        resolution_snapshots=snapshots,
        json={},
    )
    with pytest.raises(ValueError, match="private-network-endpoint|dns-rebinding-detected"):
        await adapter._request(
            client,
            "POST",
            "https://public.example/rpc",
            resolution_snapshots=snapshots,
            json={},
        )
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_explicit_trusted_and_untrusted_registration_paths(tmp_path):
    weave = AgentWeave(db_path=tmp_path / "aw.db")
    unsafe = AgentProfile(
        "unsafe",
        "Unsafe",
        [Capability("analysis")],
        metadata={"shell_access": True},
    )
    verdict = await weave.onboard_untrusted(unsafe)
    assert verdict["registered"] is False
    assert verdict["trust_path"] == "validated-untrusted"
    assert weave.registry.get("unsafe") is None

    trusted = AgentProfile("trusted", "Trusted", [Capability("analysis")])
    weave.register_trusted(trusted, reason="unit-test")
    assert weave.registry.get("trusted") is trusted
