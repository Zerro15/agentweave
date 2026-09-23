from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .models import AgentProfile
from .validation import EndpointValidation, SecurityValidator

Handler = Callable[[str], Any | Awaitable[Any]]


class A2AAdapter:
    async def invoke(self, agent: AgentProfile, task: str, context: dict | None = None) -> dict:
        raise NotImplementedError


class InMemoryA2AAdapter(A2AAdapter):
    def __init__(self):
        self.handlers = {}

    def register_handler(self, agent_id, handler: Handler):
        self.handlers[agent_id] = handler

    async def invoke(self, agent, task, context=None):
        if agent.agent_id not in self.handlers:
            raise KeyError(f"No handler for {agent.agent_id}")
        result = self.handlers[agent.agent_id](task)
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, dict) else {"result": result}


class HttpA2AAdapter(A2AAdapter):
    """A2A transport with guarded redirects and endpoint revalidation.

    Automatic redirects are disabled. Every network hop is validated immediately
    before I/O, redirect targets are revalidated, and an address-set change for the
    same hostname inside one operation is treated as a DNS-rebinding signal when the
    old and new sets do not overlap.
    """

    COMPAT_CODES = {-32601, -32602, -32005}
    REDIRECT_CODES = {307, 308}

    def __init__(
        self,
        headers=None,
        timeout=30,
        protocol_version="1.0",
        *,
        endpoint_validator: SecurityValidator | None = None,
        max_redirects: int = 3,
    ):
        self.headers = headers or {}
        self.timeout = timeout
        self.protocol_version = protocol_version
        self.endpoint_validator = endpoint_validator or SecurityValidator()
        self.max_redirects = max(0, int(max_redirects))

    def _message(self, task, context=None):
        return {
            "kind": "message",
            "messageId": str(uuid.uuid4()),
            "role": "user",
            "parts": [{"kind": "text", "text": task}],
            "metadata": context or {},
        }

    def _legacy_message(self, task, context=None):
        return {
            "messageId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": task}],
            "metadata": context or {},
        }

    @staticmethod
    def _result_or_raise(data):
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result", data)

    @staticmethod
    def _error_code(data):
        err = data.get("error") if isinstance(data, dict) else None
        return err.get("code") if isinstance(err, dict) else None

    def _endpoint_headers(self, agent, extra_headers=None):
        card = agent.metadata.get("agent_card", {})
        endpoint = agent.execution.endpoint or card.get("url")
        if not endpoint:
            raise ValueError("Agent has no A2A endpoint")
        version = str(
            agent.metadata.get("protocol_version")
            or card.get("protocolVersion")
            or self.protocol_version
        )
        return endpoint, {"A2A-Version": version, **self.headers, **(extra_headers or {})}

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

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        resolution_snapshots: dict[str, set[str]] | None = None,
        **kwargs,
    ) -> httpx.Response:
        snapshots = resolution_snapshots if resolution_snapshots is not None else {}
        current_url = url
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

            request_kwargs = dict(kwargs)
            headers = dict(request_kwargs.pop("headers", {}) or {})
            host_header = host
            if ":" in host and not host.startswith("["):
                host_header = f"[{host}]"
            if parsed.port is not None:
                host_header = f"{host_header}:{parsed.port}"
            headers["Host"] = host_header
            extensions = dict(request_kwargs.pop("extensions", {}) or {})
            extensions["sni_hostname"] = host

            response = None
            last_connect_error = None
            for address in verdict.addresses:
                address_host = f"[{address}]" if ":" in address else address
                netloc = address_host + (f":{parsed.port}" if parsed.port is not None else "")
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
                request = client.build_request(
                    method,
                    pinned_url,
                    headers=headers,
                    extensions=extensions,
                    **request_kwargs,
                )
                try:
                    response = await client.send(request, follow_redirects=False)
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
            if response.status_code not in self.REDIRECT_CODES:
                raise RuntimeError(
                    f"Refusing A2A redirect status {response.status_code}; only 307/308 preserve request semantics"
                )
            if hop >= self.max_redirects:
                raise RuntimeError("A2A redirect limit exceeded")
            current_url = urljoin(current_url, location)
        raise RuntimeError("A2A redirect limit exceeded")

    async def rpc_call(
        self,
        agent,
        method: str,
        params: dict | None = None,
        extra_headers: dict | None = None,
    ):
        endpoint, headers = self._endpoint_headers(agent, extra_headers)
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await self._request(
                client,
                "POST",
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json", **headers},
            )
            response.raise_for_status()
            return self._result_or_raise(response.json())

    async def _post_rpc(
        self,
        client,
        endpoint,
        method,
        message,
        headers,
        context=None,
        extra_params=None,
        *,
        resolution_snapshots=None,
    ):
        params = {"message": message}
        if context:
            params["metadata"] = context
        if extra_params:
            params.update(extra_params)
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        response = await self._request(
            client,
            "POST",
            endpoint,
            resolution_snapshots=resolution_snapshots,
            json=payload,
            headers={"Content-Type": "application/json", **headers},
        )
        response.raise_for_status()
        return response.json()

    async def invoke_message(
        self,
        agent,
        message: dict,
        rpc_method: str | None = None,
        context: dict | None = None,
        content_type: str | None = None,
        extra_params: dict | None = None,
        extra_headers: dict | None = None,
    ):
        card = agent.metadata.get("agent_card", {})
        endpoint, headers = self._endpoint_headers(agent, extra_headers)
        binding = str(
            agent.metadata.get("protocol_binding")
            or card.get("protocolBinding")
            or card.get("preferredTransport")
            or "JSONRPC"
        ).upper()
        snapshots: dict[str, set[str]] = {}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            if binding in {"HTTP+JSON", "REST", "HTTP_JSON"}:
                url = (
                    endpoint.rstrip("/") + "/message:send"
                    if not endpoint.rstrip("/").endswith("/message:send")
                    else endpoint
                )
                body = {"message": message}
                body.update(extra_params or {})
                response = await self._request(
                    client,
                    "POST",
                    url,
                    resolution_snapshots=snapshots,
                    json=body,
                    headers={
                        "Content-Type": content_type or "application/a2a+json",
                        **headers,
                    },
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    raise RuntimeError(str(data["error"]))
                return data
            data = await self._post_rpc(
                client,
                endpoint,
                rpc_method or "message/send",
                message,
                headers,
                context,
                extra_params,
                resolution_snapshots=snapshots,
            )
            return self._result_or_raise(data)

    async def invoke(self, agent, task, context=None):
        card = agent.metadata.get("agent_card", {})
        endpoint, headers = self._endpoint_headers(agent)
        binding = str(
            agent.metadata.get("protocol_binding")
            or card.get("protocolBinding")
            or card.get("preferredTransport")
            or "JSONRPC"
        ).upper()
        snapshots: dict[str, set[str]] = {}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            if binding in {"HTTP+JSON", "REST", "HTTP_JSON"}:
                url = (
                    endpoint.rstrip("/") + "/message:send"
                    if not endpoint.rstrip("/").endswith("/message:send")
                    else endpoint
                )
                response = await self._request(
                    client,
                    "POST",
                    url,
                    resolution_snapshots=snapshots,
                    json={"message": self._message(task, context)},
                    headers={"Content-Type": "application/a2a+json", **headers},
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    raise RuntimeError(str(data["error"]))
                return data

            attempts = [
                ("message/send", self._message(task, context), "current"),
                ("message/send", self._legacy_message(task, context), "legacy-message"),
                ("SendMessage", self._legacy_message(task, context), "legacy-method"),
            ]
            errors = []
            for method, message, label in attempts:
                data = await self._post_rpc(
                    client,
                    endpoint,
                    method,
                    message,
                    headers,
                    context,
                    resolution_snapshots=snapshots,
                )
                if "error" not in data:
                    return data.get("result", data)
                err = data.get("error") or {}
                errors.append({"profile": label, "error": err})
                if self._error_code(data) not in self.COMPAT_CODES:
                    raise RuntimeError(str(err))
            raise RuntimeError(
                str(
                    {
                        "message": "No compatible A2A wire profile succeeded",
                        "attempts": errors,
                    }
                )
            )
