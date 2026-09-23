# Road to AgentWeave 1.0

AgentWeave 0.7 establishes the canonical runtime boundary. The path to 1.0 is intentionally about evidence and API stability rather than another architecture rewrite.

## Stable core to freeze

The intended 1.0 stable surface is centered on:

- `AgentWeaveApplication`
- `AgentWeaveRuntime`
- `ToolSpec`, `ToolCall`, `ToolResult`, `ModelResponse`, `RunContext`, `RuntimeResult`
- catalog, executor, scope-policy, authorization-policy, search, and router protocols
- `RuntimeConfig`, `RuntimeFactory`, and `AgentWeaveBuilder`
- MCP composition helpers and the versioned plugin contract
- `SafeHttpTransport`

Legacy orchestration imports remain available during the pre-1.0 migration window but are not candidates for the small 1.0 root namespace unless explicitly promoted.

## Required evidence before 1.0

A 1.0 release should require all of the following:

1. Core Python CI green on every supported Python version.
2. Real upstream MCP, LangGraph, and AutoGen compatibility jobs green.
3. Real MCP server/client end-to-end execution green, including duplicate native tool names across servers.
4. Runtime red-team suite green for malformed arguments/schema, authorization bypass, deferred-discovery policy, and HTTP trust-boundary regressions.
5. Routing scale evidence through at least 100,000 catalog entries, with the host/environment recorded.
6. At least one provider-backed Issue #38 reproduction reporting real tokens and wall-clock latency; preferably multiple provider/model pairs.
7. Installation smoke test from the built wheel, including `[mcp]`, `[langgraph]`, and `[autogen]` extras.
8. A migration guide covering every root-level removal or rename.

## Compatibility discipline

- 0.7.x: add tests and compatibility shims; avoid new architectural layers.
- 0.8.x: collect integration feedback and deprecate accidental public surfaces with actionable warnings.
- 0.9.x: freeze the proposed 1.0 API, stop adding root exports, and run release-candidate compatibility tests.
- 1.0.0: remove only items that were explicitly deprecated and documented; publish a migration guide and immutable evidence links.

Unsafe behavior may still be tightened without preserving an insecure compatibility path.

## Release gates

Every release candidate should validate:

```text
source tests
  -> upstream integration compatibility
  -> MCP end-to-end proof
  -> runtime security/red-team proof
  -> package build + wheel install smoke
  -> release metadata consistency
  -> immutable published release (repository setting enabled)
```

Provider-backed research evidence should not block a patch release for a code/security fix, but 1.0 should not be declared until at least one real-provider reproduction exists.
