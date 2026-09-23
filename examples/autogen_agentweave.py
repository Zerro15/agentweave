from __future__ import annotations

import asyncio
from typing import Sequence

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken

from agentweave import AgentWeaveRuntime, CallableExecutor, StaticToolCatalog
from agentweave.integrations.autogen import AgentWeaveAutoGenSelector


class SpecialistAgent(BaseChatAgent):
    """Small deterministic AutoGen agent used to demonstrate participant selection."""

    def __init__(self, name: str, description: str) -> None:
        super().__init__(name=name, description=description)

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        task = ""
        for message in reversed(messages):
            if isinstance(message, TextMessage):
                task = str(message.content)
                break
        return Response(
            chat_message=TextMessage(
                source=self.name,
                content=f"{self.name} received the routed task: {task}",
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None


class _UnusedModel:
    async def complete(self, messages, *, tools=None, **kwargs):
        raise RuntimeError("Participant selection does not invoke the runtime model")


def build_runtime() -> AgentWeaveRuntime:
    return AgentWeaveRuntime(
        model=_UnusedModel(),
        catalog=StaticToolCatalog([]),
        executor=CallableExecutor({}),
        max_tools=2,
    )


async def main() -> None:
    task = "Review this backend API design and database query strategy."
    descriptions = {
        "backend_specialist": "Reviews backend services, APIs, and application architecture.",
        "database_specialist": "Reviews databases, SQL, schemas, and query design.",
        "research_specialist": "Handles research, analysis, and summarization tasks.",
        "mcp_specialist": "Handles MCP, tool-use, and integration questions.",
    }

    selector = AgentWeaveAutoGenSelector(
        build_runtime(),
        descriptions,
        max_participants=2,
    )
    selected_names = await selector.select(task)

    all_autogen_agents = {
        name: SpecialistAgent(name, description)
        for name, description in descriptions.items()
    }
    selected_agents = [all_autogen_agents[name] for name in selected_names]
    team = RoundRobinGroupChat(
        selected_agents,
        max_turns=max(1, len(selected_agents)),
    )
    result = await team.run(task=task)

    print("AgentWeave selected AutoGen participants:", selected_names)
    print("AutoGen messages:")
    for message in result.messages:
        source = getattr(message, "source", "unknown")
        content = getattr(message, "content", "")
        print(f"- {source}: {content}")


if __name__ == "__main__":
    asyncio.run(main())
