from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ToolSpec:
    """Provider-neutral tool descriptor used throughout the AgentWeave runtime."""

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    id: str | None = None
    provider: str | None = None
    source: str | None = None
    risk_level: str = "standard"
    permissions: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    tenants: frozenset[str] = frozenset()
    environments: frozenset[str] = frozenset()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    native: Any = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> str:
        return self.id or self.name

    def to_function_tool(self) -> dict[str, Any]:
        """Return an OpenAI-compatible function-tool descriptor."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    id: str | None = None
    provider: str | None = None
    raw: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str | None
    name: str
    success: bool
    content: Any = None
    structured_content: Any = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    raw: Any = field(default=None, compare=False, repr=False)

    def model_content(self) -> str:
        value = self.structured_content
        if value is None:
            value = self.content
        if value is None and self.error:
            value = {"error": self.error}
        return str(value if value is not None else "")


@dataclass(frozen=True)
class ModelResponse:
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class RunContext:
    """Security and tenancy context carried through every runtime stage."""

    identity: str | None = None
    role: str | None = None
    tenant: str | None = None
    permissions: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    environment: str | None = None
    risk_tier: str = "standard"
    human_approved: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    response: ModelResponse | None
    tool_results: tuple[ToolResult, ...]
    selected_tools: tuple[str, ...]
    routing_confidence: float | None = None
    routing_abstained: bool = False
    recovery_attempts: int = 0
    provenance: Mapping[str, Any] = field(default_factory=dict)
