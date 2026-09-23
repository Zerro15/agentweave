from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .validation import EndpointValidation, SecurityValidator


class SafeHttpTransport:
    """Centralized HTTP transport with SSRF, redirect, DNS and credential controls.

    Every hop is validated immediately before I/O. DNS answers are pinned into the
    request URL while preserving Host/SNI, cross-origin redirects lose credential
    headers, and non-idempotent requests only follow 307/308 redirects.
    """

    SENSITIVE_REDIRECT_HEADERS = {
        "authorization",
        "cookie",
        "proxy-authorization",
    }

    def __init__(
        self,
        *,
        timeout: float | None = 30.0,
        endpoint_validator: SecurityValidator | None = None,
        max_redirects: int = 3,
    ) -> None:
        self.timeout = timeout
        self.endpoint_validator = endpoint_validator or SecurityValidator()
        self.max_redirects = max(0, int(max_redirects))

    @staticmethod
    def _origin(parsed) -> tuple[str, str, int]:
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        return scheme, host, port

    @staticmethod
    def _remember_resolution(
        verdict: EndpointValidation,
        snapshots: dict[str, set[str]],
    ) -> None:
        if not verdict.host or not verdict.addresses:
            return
        current = set(verdict.addresses)
        previous = snapshots.get(verdict.host)
        if previous and not (previous & current):
            raise ValueError("unsafe endpoint: dns-rebinding-detected")
        snapshots[verdict.host] = current if previous is None else previous | current

    @staticmethod
    def _redirect_allowed(method: str, status_code: int) -> bool:
        method = method.upper()
        if status_code in {307, 308}:
            return True
        return method in {"GET", "HEAD"} and status_code in {301, 302, 303}

    async def request(
        self,
        method: str,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        resolution_snapshots: dict[str, set[str]] | None = None,
        stream_response: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        own_client = client is None
        if own_client and stream_response:
            raise ValueError(
                "stream_response=True requires a caller-owned AsyncClient so the "
                "response can remain open while it is consumed"
            )
        active_client = client or httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
        )
        snapshots = resolution_snapshots if resolution_snapshots is not None else {}
        current_url = url
        credential_origin = None
        try:
            for hop in range(self.max_redirects + 1):
                verdict = self.endpoint_validator.assert_safe_endpoint(
                    current_url,
                    require_resolved=True,
                )
                self._remember_resolution(verdict, snapshots)
                parsed = urlparse(current_url)
                host = parsed.hostname
                if not host or not verdict.addresses:
                    raise ValueError("unsafe endpoint: endpoint-resolution-failed")

                current_origin = self._origin(parsed)
                if credential_origin is None:
                    credential_origin = current_origin

                request_kwargs = dict(kwargs)
                headers = dict(request_kwargs.pop("headers", {}) or {})
                if current_origin != credential_origin:
                    headers = {
                        key: value
                        for key, value in headers.items()
                        if key.lower() not in self.SENSITIVE_REDIRECT_HEADERS
                    }

                host_header = host
                if ":" in host and not host.startswith("["):
                    host_header = f"[{host}]"
                if parsed.port is not None:
                    host_header = f"{host_header}:{parsed.port}"
                headers["Host"] = host_header
                extensions = dict(request_kwargs.pop("extensions", {}) or {})
                extensions["sni_hostname"] = host

                response = None
                last_connect_error: Exception | None = None
                for address in verdict.addresses:
                    address_host = f"[{address}]" if ":" in address else address
                    netloc = address_host + (
                        f":{parsed.port}" if parsed.port is not None else ""
                    )
                    pinned_url = urlunparse(
                        (
                            parsed.scheme,
                            netloc,
                            parsed.path,
                            parsed.params,
                            parsed.query,
                            parsed.fragment,
                        )
                    )
                    request = active_client.build_request(
                        method,
                        pinned_url,
                        headers=headers,
                        extensions=extensions,
                        **request_kwargs,
                    )
                    send_kwargs: dict[str, Any] = {"follow_redirects": False}
                    if stream_response:
                        send_kwargs["stream"] = True
                    try:
                        response = await active_client.send(request, **send_kwargs)
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        last_connect_error = exc

                if response is None:
                    if last_connect_error is not None:
                        raise last_connect_error
                    raise RuntimeError("No validated endpoint address was connectable")

                if response.status_code not in range(300, 400):
                    return response
                location = response.headers.get("location")
                if not location:
                    return response
                if not self._redirect_allowed(method, response.status_code):
                    await response.aclose()
                    raise RuntimeError(
                        f"Refusing redirect status {response.status_code} for {method}"
                    )
                if hop >= self.max_redirects:
                    await response.aclose()
                    raise RuntimeError("HTTP redirect limit exceeded")
                await response.aclose()
                current_url = urljoin(current_url, location)
            raise RuntimeError("HTTP redirect limit exceeded")
        finally:
            if own_client:
                await active_client.aclose()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)
