from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentweave.requirements import RequirementAnalyzer

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*")


def tool_name(tool: Mapping[str, Any]) -> str:
    fn = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    return str(fn.get("name") or "tool")


def tool_text(tool: Mapping[str, Any]) -> str:
    fn = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    schema = fn.get("parameters", fn.get("inputSchema", {}))
    return " ".join(
        part
        for part in (
            str(fn.get("name") or ""),
            str(fn.get("description") or ""),
            json.dumps(schema, sort_keys=True, default=str),
        )
        if part
    )


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


@dataclass(frozen=True)
class ToolRoutingResult:
    selected: list[Mapping[str, Any]]
    filtered: list[Mapping[str, Any]]
    provenance: dict[str, Any]
    confidence: float = 1.0
    abstained: bool = False


@runtime_checkable
class Router(Protocol):
    """Provider-neutral synchronous routing contract."""

    version: str

    def route(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int = 8,
    ) -> ToolRoutingResult:
        ...


@runtime_checkable
class AsyncRouter(Protocol):
    """Provider-neutral asynchronous routing contract."""

    version: str

    async def aroute(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int = 8,
    ) -> ToolRoutingResult:
        ...


class ToolSearchProvider(Protocol):
    """Deferred discovery contract; ``search`` may be sync or async."""

    def search(
        self,
        text: str,
        *,
        excluded_names: set[str],
        limit: int,
    ) -> Any:
        ...


@dataclass(frozen=True)
class ConfidencePolicy:
    """Controls when the adaptive router abstains from aggressive pruning."""

    min_confidence: float = 0.50
    expansion_factor: int = 2
    max_abstention_tools: int = 24
    search_limit: int = 16

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if self.expansion_factor < 1:
            raise ValueError("expansion_factor must be at least 1")
        if self.max_abstention_tools < 1:
            raise ValueError("max_abstention_tools must be at least 1")
        if self.search_limit < 1:
            raise ValueError("search_limit must be at least 1")


class DeterministicRouterV1:
    """Original deterministic lexical router behind sync and async contracts."""

    version = "agentweave-tool-router-v1"

    def __init__(self, analyzer: RequirementAnalyzer | None = None):
        self.analyzer = analyzer or RequirementAnalyzer()

    def _rank(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> tuple[list[tuple[float, str, int, Mapping[str, Any]]], set[str], set[str]]:
        req = self.analyzer.analyze(text)
        query_tokens = _tokens(text)
        capability_tokens = {c.lower() for c in req.capabilities}
        scored: list[tuple[float, str, int, Mapping[str, Any]]] = []
        for index, tool in enumerate(tools):
            name = tool_name(tool)
            rendered = tool_text(tool)
            rendered_lower = rendered.lower()
            text_tokens = _tokens(rendered)
            lexical = len(query_tokens & text_tokens)
            capability = sum(
                2.0
                for cap in capability_tokens
                if cap in text_tokens or cap in rendered_lower
            )
            exact_name = 2.0 if name.lower() in text.lower() else 0.0
            score = float(lexical) + capability + exact_name
            scored.append((score, name.lower(), index, tool))
        return (
            sorted(scored, key=lambda row: (-row[0], row[1], row[2])),
            query_tokens,
            capability_tokens,
        )

    @staticmethod
    def _confidence(
        ranked: Sequence[tuple[float, str, int, Mapping[str, Any]]],
    ) -> tuple[float, float]:
        if not ranked or ranked[0][0] <= 0:
            return 0.0, 0.0
        top = float(ranked[0][0])
        second = float(ranked[1][0]) if len(ranked) > 1 else 0.0
        margin = max(0.0, (top - second) / max(1.0, top))
        evidence = top / (top + 2.0)
        return min(1.0, 0.70 * evidence + 0.30 * margin), margin

    def route(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int = 8,
    ) -> ToolRoutingResult:
        if max_tools < 1:
            raise ValueError("max_tools must be at least 1")
        catalog = list(tools)
        ranked, _, _ = self._rank(text, catalog)
        selected_rows = ranked[: min(max_tools, len(ranked))]
        selected_indices = {row[2] for row in selected_rows}
        selected = [row[3] for row in selected_rows]
        filtered = [
            tool for index, tool in enumerate(catalog) if index not in selected_indices
        ]
        confidence, margin = self._confidence(ranked)
        catalog_fingerprint = hashlib.sha256(
            json.dumps(
                catalog,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        req = self.analyzer.analyze(text)
        provenance = {
            "router": self.version,
            "router_family": "deterministic-lexical",
            "catalog_sha256": catalog_fingerprint,
            "catalog_size": len(catalog),
            "max_tools": max_tools,
            "required_capabilities": sorted(req.capabilities),
            "selected_tools": [tool_name(t) for t in selected],
            "filtered_tools": [tool_name(t) for t in filtered],
            "scores": [{"tool": row[1], "score": row[0]} for row in ranked],
            "routing_confidence": confidence,
            "confidence_method": "top-score-and-margin-heuristic-v1",
            "top_two_margin": margin,
            "confidence_is_probability": False,
            "abstained": False,
        }
        return ToolRoutingResult(
            selected=selected,
            filtered=filtered,
            provenance=provenance,
            confidence=confidence,
            abstained=False,
        )

    async def aroute(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int = 8,
    ) -> ToolRoutingResult:
        return self.route(text, tools, max_tools=max_tools)


class AdaptiveRouter:
    """Confidence-aware wrapper with sync and async deferred-discovery paths."""

    version = "agentweave-adaptive-router-v1"

    def __init__(
        self,
        base_router: Router | AsyncRouter | None = None,
        *,
        confidence_policy: ConfidencePolicy | None = None,
        search_provider: ToolSearchProvider | None = None,
    ):
        self.base_router = base_router or DeterministicRouterV1()
        self.confidence_policy = confidence_policy or ConfidencePolicy()
        self.search_provider = search_provider

    @staticmethod
    def _merge_catalog(
        catalog: Sequence[Mapping[str, Any]],
        discovered: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        merged = list(catalog)
        seen = {tool_name(tool).lower() for tool in merged}
        for tool in discovered:
            name = tool_name(tool).lower()
            if name not in seen:
                merged.append(tool)
                seen.add(name)
        return merged

    @staticmethod
    def _confident(
        initial: ToolRoutingResult,
        *,
        base_router: Any,
        policy: ConfidencePolicy,
        max_tools: int,
    ) -> ToolRoutingResult:
        provenance = dict(initial.provenance)
        provenance.update(
            {
                "router": AdaptiveRouter.version,
                "base_router": getattr(
                    base_router,
                    "version",
                    type(base_router).__name__,
                ),
                "adaptive_decision": "confident-prune",
                "confidence_threshold": policy.min_confidence,
                "initial_budget": max_tools,
                "final_budget": len(initial.selected),
                "search_invoked": False,
            }
        )
        return ToolRoutingResult(
            selected=initial.selected,
            filtered=initial.filtered,
            provenance=provenance,
            confidence=initial.confidence,
            abstained=False,
        )

    @staticmethod
    def _expanded_budget(
        catalog_size: int,
        max_tools: int,
        policy: ConfidencePolicy,
    ) -> int:
        return max(
            1,
            min(
                catalog_size if catalog_size else max_tools,
                max(max_tools + 1, max_tools * policy.expansion_factor),
                policy.max_abstention_tools,
            ),
        )

    def _sync_base(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int,
    ) -> ToolRoutingResult:
        method = getattr(self.base_router, "route", None)
        if method is None:
            raise TypeError("async-only base router requires AdaptiveRouter.aroute()")
        result = method(text, tools, max_tools=max_tools)
        if inspect.isawaitable(result):
            raise TypeError("async base router requires AdaptiveRouter.aroute()")
        return result

    async def _async_base(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int,
    ) -> ToolRoutingResult:
        method = getattr(self.base_router, "aroute", None) or getattr(
            self.base_router, "route", None
        )
        if method is None:
            raise TypeError("base router must define route() or aroute()")
        result = method(text, tools, max_tools=max_tools)
        if inspect.isawaitable(result):
            result = await result
        return result

    @staticmethod
    def _finalize_expanded(
        expanded: ToolRoutingResult,
        *,
        initial: ToolRoutingResult,
        base_router: Any,
        policy: ConfidencePolicy,
        max_tools: int,
        search_invoked: bool,
        discovered_count: int,
    ) -> ToolRoutingResult:
        provenance = dict(expanded.provenance)
        provenance.update(
            {
                "router": AdaptiveRouter.version,
                "base_router": getattr(
                    base_router,
                    "version",
                    type(base_router).__name__,
                ),
                "adaptive_decision": (
                    "abstain-search-expand" if search_invoked else "abstain-expand"
                ),
                "confidence_threshold": policy.min_confidence,
                "initial_confidence": initial.confidence,
                "final_confidence": expanded.confidence,
                "initial_budget": max_tools,
                "final_budget": len(expanded.selected),
                "search_invoked": search_invoked,
                "discovered_tools": discovered_count,
                "abstained": True,
                "abstention_semantics": "avoid-aggressive-pruning",
            }
        )
        return ToolRoutingResult(
            selected=expanded.selected,
            filtered=expanded.filtered,
            provenance=provenance,
            confidence=expanded.confidence,
            abstained=True,
        )

    def route(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int = 8,
    ) -> ToolRoutingResult:
        if max_tools < 1:
            raise ValueError("max_tools must be at least 1")
        catalog = list(tools)
        initial = self._sync_base(text, catalog, max_tools=max_tools)
        policy = self.confidence_policy
        if initial.confidence >= policy.min_confidence:
            return self._confident(
                initial,
                base_router=self.base_router,
                policy=policy,
                max_tools=max_tools,
            )

        expanded_budget = self._expanded_budget(len(catalog), max_tools, policy)
        search_invoked = False
        discovered_count = 0
        working_catalog = catalog
        if self.search_provider is not None:
            discovered = self.search_provider.search(
                text,
                excluded_names={tool_name(t).lower() for t in catalog},
                limit=policy.search_limit,
            )
            if inspect.isawaitable(discovered):
                raise TypeError(
                    "async search provider requires await AdaptiveRouter.aroute(...)"
                )
            search_invoked = True
            discovered_list = list(discovered or [])
            discovered_count = len(discovered_list)
            working_catalog = self._merge_catalog(catalog, discovered_list)
            expanded_budget = min(
                max(expanded_budget, max_tools + discovered_count),
                len(working_catalog),
                policy.max_abstention_tools,
            )

        expanded = self._sync_base(
            text,
            working_catalog,
            max_tools=expanded_budget,
        )
        return self._finalize_expanded(
            expanded,
            initial=initial,
            base_router=self.base_router,
            policy=policy,
            max_tools=max_tools,
            search_invoked=search_invoked,
            discovered_count=discovered_count,
        )

    async def aroute(
        self,
        text: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        max_tools: int = 8,
    ) -> ToolRoutingResult:
        if max_tools < 1:
            raise ValueError("max_tools must be at least 1")
        catalog = list(tools)
        initial = await self._async_base(text, catalog, max_tools=max_tools)
        policy = self.confidence_policy
        if initial.confidence >= policy.min_confidence:
            return self._confident(
                initial,
                base_router=self.base_router,
                policy=policy,
                max_tools=max_tools,
            )

        expanded_budget = self._expanded_budget(len(catalog), max_tools, policy)
        search_invoked = False
        discovered_count = 0
        working_catalog = catalog
        if self.search_provider is not None:
            discovered = self.search_provider.search(
                text,
                excluded_names={tool_name(t).lower() for t in catalog},
                limit=policy.search_limit,
            )
            if inspect.isawaitable(discovered):
                discovered = await discovered
            search_invoked = True
            discovered_list = list(discovered or [])
            discovered_count = len(discovered_list)
            working_catalog = self._merge_catalog(catalog, discovered_list)
            expanded_budget = min(
                max(expanded_budget, max_tools + discovered_count),
                len(working_catalog),
                policy.max_abstention_tools,
            )

        expanded = await self._async_base(
            text,
            working_catalog,
            max_tools=expanded_budget,
        )
        return self._finalize_expanded(
            expanded,
            initial=initial,
            base_router=self.base_router,
            policy=policy,
            max_tools=max_tools,
            search_invoked=search_invoked,
            discovered_count=discovered_count,
        )


ToolRouter = DeterministicRouterV1
