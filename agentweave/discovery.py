from __future__ import annotations

from .models import AgentProfile, Capability, ExecutionProfile
from .safe_http import SafeHttpTransport


def _first_interface(card: dict) -> dict:
    interfaces = (
        card.get("supportedInterfaces")
        or card.get("supported_interfaces")
        or card.get("interfaces")
        or []
    )
    if interfaces and isinstance(interfaces[0], dict):
        return interfaces[0]
    return {}


class AgentCardDiscovery:
    def __init__(self, *, transport: SafeHttpTransport | None = None):
        self.transport = transport or SafeHttpTransport(timeout=20)

    async def fetch(self, url: str) -> AgentProfile:
        response = await self.transport.get(url)
        response.raise_for_status()
        card = response.json()
        caps = []
        skills = card.get("skills", []) or card.get("capabilities", [])
        for skill in skills:
            if isinstance(skill, str):
                caps.append(Capability(skill))
            elif isinstance(skill, dict):
                caps.append(
                    Capability(
                        skill.get("id") or skill.get("name") or "unknown",
                        float(skill.get("proficiency", 0.5)),
                    )
                )
        interface = _first_interface(card)
        endpoint = (
            interface.get("url")
            or interface.get("endpoint")
            or card.get("url")
            or card.get("endpoint")
        )
        binding = (
            interface.get("protocolBinding")
            or interface.get("protocol_binding")
            or card.get("protocolBinding")
            or card.get("preferredTransport")
            or "JSONRPC"
        )
        protocol_version = (
            interface.get("protocolVersion")
            or interface.get("protocol_version")
            or card.get("protocolVersion")
            or "1.0"
        )
        return AgentProfile(
            agent_id=str(card.get("id") or card.get("name") or endpoint or url),
            name=card.get("name", "A2A Agent"),
            capabilities=caps,
            domains=list(card.get("domains", [])),
            knowledge=list(card.get("knowledge", [])),
            execution=ExecutionProfile(
                location=card.get("location", "cloud"),
                endpoint=endpoint,
            ),
            metadata={
                "agent_card": card,
                "agent_card_url": url,
                "protocol_binding": binding,
                "protocol_version": protocol_version,
                "streaming": bool(
                    (card.get("capabilities") or {}).get("streaming", False)
                ),
            },
        )


class HttpMarketplace:
    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        transport: SafeHttpTransport | None = None,
    ):
        self.url = url
        self.token = token
        self.transport = transport or SafeHttpTransport(timeout=20)

    async def list_agents(self):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = await self.transport.get(self.url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("agents", payload) if isinstance(payload, dict) else payload
        out = []
        for item in items:
            caps = [
                Capability(
                    capability if isinstance(capability, str) else capability.get("name", "unknown"),
                    0.5
                    if isinstance(capability, str)
                    else float(capability.get("proficiency", 0.5)),
                )
                for capability in item.get("capabilities", item.get("skills", []))
            ]
            out.append(
                AgentProfile(
                    agent_id=str(item.get("id") or item.get("name")),
                    name=item.get("name", "Marketplace Agent"),
                    capabilities=caps,
                    domains=list(item.get("domains", [])),
                    knowledge=list(item.get("knowledge", [])),
                    execution=ExecutionProfile(
                        location=item.get("location", "cloud"),
                        endpoint=item.get("endpoint") or item.get("url"),
                    ),
                    metadata={"marketplace": item},
                )
            )
        return out


class StaticMarketplace:
    def __init__(self, agents):
        self.agents = list(agents)

    async def list_agents(self):
        return list(self.agents)
