from __future__ import annotations

from .discovery import AgentCardDiscovery
from .models import AgentProfile, Capability, ExecutionProfile
from .safe_http import SafeHttpTransport


class AWSBedrockAgentConnector:
    """Lists agents from the caller's Amazon Bedrock account via boto3."""

    def __init__(self, region_name=None, client=None):
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("Install agentweave-router[aws]") from exc
            client = boto3.client("bedrock-agent", region_name=region_name)
        self.client = client

    async def list_agents(self):
        token = None
        out = []
        while True:
            kwargs = {"maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            page = self.client.list_agents(**kwargs)
            for item in page.get("agentSummaries", []):
                caps = [Capability("reasoning", 0.5, False)]
                out.append(
                    AgentProfile(
                        str(item.get("agentId")),
                        item.get("agentName", "Bedrock Agent"),
                        caps,
                        domains=["aws-bedrock"],
                        execution=ExecutionProfile(location="cloud"),
                        metadata={"ecosystem": "aws-bedrock", "summary": item},
                    )
                )
            token = page.get("nextToken")
            if not token:
                break
        return out


class MicrosoftFoundryAgentConnector:
    """Microsoft Foundry Agents REST connector using SafeHttpTransport."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        api_version: str,
        *,
        transport: SafeHttpTransport | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.api_version = api_version
        self.transport = transport or SafeHttpTransport(timeout=30)

    async def list_agents(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        out = []
        url = f"{self.endpoint}/agents"
        while url:
            response = await self.transport.get(
                url,
                params={"api-version": self.api_version},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("value") or data.get("agents") or []
            for item in items:
                latest = ((item.get("versions") or {}).get("latest") or {})
                card = item.get("agent_card") or latest.get("agent_card") or {}
                skills = card.get("skills") or []
                caps = [
                    Capability(
                        skill.get("id") or skill.get("name") or "unknown",
                        0.5,
                        False,
                    )
                    if isinstance(skill, dict)
                    else Capability(str(skill))
                    for skill in skills
                ]
                endpoint = (
                    (item.get("agent_endpoint") or {}).get("url")
                    or latest.get("endpoint")
                )
                out.append(
                    AgentProfile(
                        str(item.get("id") or item.get("name")),
                        item.get("name", "Foundry Agent"),
                        caps or [Capability("reasoning")],
                        domains=["microsoft-foundry"],
                        execution=ExecutionProfile(location="cloud", endpoint=endpoint),
                        metadata={
                            "ecosystem": "microsoft-foundry",
                            "agent_card": card,
                            "raw": item,
                        },
                    )
                )
            url = data.get("nextLink")
        return out


class GoogleCloudMarketplaceA2AConnector:
    """Loads procured Google Cloud Marketplace A2A Agent Cards."""

    def __init__(
        self,
        agent_card_urls: list[str],
        *,
        transport: SafeHttpTransport | None = None,
    ):
        self.urls = list(agent_card_urls)
        self.discovery = AgentCardDiscovery(transport=transport)

    async def list_agents(self):
        out = []
        for url in self.urls:
            agent = await self.discovery.fetch(url)
            agent.metadata["ecosystem"] = "google-cloud-marketplace"
            agent.metadata["marketplace_card_url"] = url
            out.append(agent)
        return out


class CatalogManifestConnector:
    """Connector for curated enterprise A2A catalogs with explicit card URLs."""

    def __init__(
        self,
        manifest_url: str,
        token: str | None = None,
        *,
        transport: SafeHttpTransport | None = None,
    ):
        self.url = manifest_url
        self.token = token
        self.transport = transport or SafeHttpTransport(timeout=30)
        self.discovery = AgentCardDiscovery(transport=self.transport)

    async def list_agents(self):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = await self.transport.get(self.url, headers=headers)
        response.raise_for_status()
        data = response.json()
        cards = data.get("agentCards") or data.get("agents") or data
        out = []
        for item in cards:
            url = (
                item
                if isinstance(item, str)
                else item.get("agentCardUrl") or item.get("url")
            )
            if url:
                out.append(await self.discovery.fetch(url))
        return out
