import pytest

from agentweave import SafeHttpTransport
from agentweave.validation import SecurityValidator


class Response:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    async def aclose(self):
        self.closed = True


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_request(self, method, url, **kwargs):
        return (method, url, kwargs)

    async def send(self, request, *, follow_redirects=False):
        self.calls.append((request, follow_redirects))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_safe_http_blocks_private_redirect_and_pins_dns():
    def resolver(host):
        return {
            "public.example": {"93.184.216.34"},
            "metadata.internal": {"169.254.169.254"},
        }[host]

    transport = SafeHttpTransport(
        endpoint_validator=SecurityValidator(resolver=resolver),
        max_redirects=2,
    )
    client = Client(
        [Response(307, {"location": "https://metadata.internal/latest"})]
    )
    with pytest.raises(ValueError, match="private-network-endpoint"):
        await transport.request(
            "POST",
            "https://public.example/rpc",
            client=client,
            json={},
        )
    request, follow_redirects = client.calls[0]
    assert "93.184.216.34" in request[1]
    assert request[2]["headers"]["Host"] == "public.example"
    assert request[2]["extensions"]["sni_hostname"] == "public.example"
    assert follow_redirects is False


@pytest.mark.asyncio
async def test_safe_http_strips_credentials_on_cross_origin_redirect():
    def resolver(host):
        return {
            "public.example": {"93.184.216.34"},
            "other.example": {"93.184.216.35"},
        }[host]

    transport = SafeHttpTransport(
        endpoint_validator=SecurityValidator(resolver=resolver),
        max_redirects=2,
    )
    client = Client(
        [
            Response(307, {"location": "https://other.example/rpc"}),
            Response(200),
        ]
    )
    await transport.request(
        "POST",
        "https://public.example/rpc",
        client=client,
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Request-ID": "r1",
        },
        json={},
    )
    second_headers = client.calls[1][0][2]["headers"]
    assert "Authorization" not in second_headers
    assert "Cookie" not in second_headers
    assert second_headers["X-Request-ID"] == "r1"
    assert second_headers["Host"] == "other.example"
