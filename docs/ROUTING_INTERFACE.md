# Routing interface, confidence, and abstention

AgentWeave BYOM routing now uses a formal `Router` protocol. A router receives a task, a tool catalog, and a maximum tool budget and returns a `ToolRoutingResult` containing the selected/filtered descriptors, provenance, a bounded confidence signal, and whether the router abstained from aggressive pruning.

## Routers

- `DeterministicRouterV1` is the original provider-neutral lexical/capability router. `ToolRouter` remains a backward-compatible alias to this class.
- `AdaptiveRouter` wraps any `Router` and applies a `ConfidencePolicy`.

The default BYOM path is `AdaptiveRouter(DeterministicRouterV1(...))`.

## Confidence semantics

The v1 deterministic confidence is a stable top-score/margin heuristic. It is **not a correctness probability** and provenance explicitly records `confidence_is_probability: false`.

When confidence is below the configured threshold, `AdaptiveRouter` abstains from aggressive pruning. It expands the bounded candidate set and, when a `ToolSearchProvider` is configured, performs deferred discovery before finalizing the larger set.

Abstention therefore means "do not trust a narrow top-k enough to hide alternatives" rather than "return no tools".

## Policy boundary

Relevance routing and deferred search never grant execution permission. Deterministic scope/policy filtering and the post-selection authorization gate remain separate security controls.
