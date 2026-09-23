from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..runtime import AgentWeaveRuntime
from ..runtime_types import RunContext, ToolSpec


class AgentWeaveAutoGenSelector:
    """Select AutoGen participants through the public AgentWeave runtime API only."""

    def __init__(
        self,
        runtime: AgentWeaveRuntime,
        participants: Mapping[str, str],
        *,
        max_participants: int = 2,
    ) -> None:
        self.runtime = runtime
        self.participants = dict(participants)
        self.max_participants = max(1, int(max_participants))

    def _participant_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                id=f"autogen:{name}",
                name=name,
                description=description,
                provider="autogen",
                source="participants",
            )
            for name, description in self.participants.items()
        ]

    async def select(
        self,
        task: str,
        *,
        context: RunContext | None = None,
    ) -> list[str]:
        original_max_tools = self.runtime.max_tools
        try:
            self.runtime.max_tools = self.max_participants
            preview = await self.runtime.preview_route(
                task,
                context=context or RunContext(),
                tools=self._participant_tools(),
            )
        finally:
            self.runtime.max_tools = original_max_tools
        return [tool.name for tool in preview.selected]

    async def selector_func(self, messages: Sequence[Any]) -> str | None:
        """AutoGen-style async selector callback returning the next participant name."""
        if not messages:
            return None
        last = messages[-1]
        content = getattr(last, "content", None)
        if content is None and isinstance(last, Mapping):
            content = last.get("content")
        selected = await self.select(str(content or ""))
        return selected[0] if selected else None
