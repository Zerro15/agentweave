# API Compatibility Policy

AgentWeave uses Semantic Versioning for the Python distribution.

## Distribution and import names

The distribution name is `agentweave-router` while the Python import package remains `agentweave`:

```bash
pip install agentweave-router
```

```python
import agentweave
```

This avoids the unrelated `agentweave` distribution name on PyPI without forcing application imports to change.

## Pre-1.0 stable root surface

Before 1.0, the explicitly supported root surface is the curated `agentweave.__all__` list. It is intentionally small and centered on:

- `AgentWeave` and `AgentWeaveRuntime`;
- normalized runtime contracts such as `ToolSpec`, `ToolCall`, `ToolResult`, `ModelResponse`, `RunContext`, and `RuntimeResult`;
- runtime builder/configuration contracts;
- catalog/executor/scope/search interfaces;
- `SafeHttpTransport`;
- typed plugin contracts.

The goal is to keep integrations independent from internal matcher, graph, persistence, benchmark, transport-proof, and evaluation implementation details.

## Legacy root imports

Historical names such as `AgentProfile`, `Capability`, `InMemoryA2AAdapter`, benchmark helpers, and lower-level engines remain resolvable from the package root through compatibility shims during the pre-1.0 migration. They emit `DeprecationWarning` because they are not part of the new stable root promise.

New code should either use the canonical runtime surface or import advanced/experimental classes from their defining submodule.

## Experimental surface

Implementation-detail modules, CI scripts, research/evaluation code, proof harnesses, generated benchmark artifacts, and advanced protocol helpers may evolve in minor releases. Pin a package version if depending directly on them.

## Deprecation

Where practical, behavior that was previously public remains available for at least one migration window and emits a deprecation notice naming the preferred surface. Unsafe behavior may be changed without preserving an insecure compatibility path.

## Protocol compatibility

A2A and MCP protocol compatibility are versioned independently from AgentWeave. Integrations should advertise/accept the protocol version appropriate to the remote endpoint and validate against upstream conformance tools where applicable.
