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

## 0.7 canonical runtime

New applications should start with `AgentWeaveRuntime` or lifecycle-owned `AgentWeaveApplication`.

The canonical security/execution sequence is:

```text
catalog → scope → routing → model → schema validation → authorization → executor → recovery
```

The runtime contracts intentionally separate stable tool identity from the function name shown to a model. `ToolSpec.key` identifies the provider/source tool; `ToolSpec.model_name` can supply a distinct model-visible alias when several sources expose the same native name. Duplicate model-visible names fail loudly instead of silently selecting one provider.

`ToolCall` and `ToolResult` carry the resolved tool identity through execution. Model arguments are validated against `ToolSpec.input_schema` before authorization. Custom authorization policies can implement `authorize_tool(call=..., tool=..., context=...)` to inspect arguments and full tool/provider metadata; legacy `authorize(action=..., context=...)` policies remain supported.

## Pre-1.0 stable root surface

Before 1.0, the explicitly supported root surface is the curated `agentweave.__all__` list. It is intentionally centered on:

- `AgentWeave`, `AgentWeaveRuntime`, and `AgentWeaveApplication`;
- normalized runtime contracts such as `ToolSpec`, `ToolCall`, `ToolResult`, `ModelResponse`, `RunContext`, `RuntimeResult`, and runtime telemetry;
- runtime builder/configuration contracts;
- catalog/executor/scope/search/authorization interfaces;
- `SafeHttpTransport`;
- typed plugin contracts.

The goal is to keep integrations independent from internal matcher, graph, persistence, benchmark, transport-proof, and evaluation implementation details.

## Legacy root imports

Historical names such as `AgentProfile`, `Capability`, `InMemoryA2AAdapter`, benchmark helpers, and lower-level engines remain resolvable from the package root through compatibility shims during the pre-1.0 migration. They emit `DeprecationWarning` because they are not part of the new stable root promise.

New code should either use the canonical runtime surface or import advanced/experimental classes from their defining submodule.

## Plugin API

Plugins target the major `PLUGIN_API_VERSION`. Duplicate component/plugin registration fails rather than silently replacing an earlier implementation. Plugin startup is transactional and is owned automatically by `AgentWeaveApplication`; failed partial startup is rolled back before the application returns control.

## Integration compatibility

MCP, LangGraph, AutoGen, and A2A protocol/framework compatibility are versioned independently from AgentWeave. Their supported dependency ranges are declared as optional extras. Real supported MCP/LangGraph/AutoGen packages are installed in dedicated compatibility CI so upstream API drift is distinguished from unit-test-double regressions.

For MCP, AgentWeave can own a reusable session lifecycle through `MCPConnection`. HTTP MCP targets are endpoint-validated before connection establishment; the MCP SDK still owns its protocol wire transport unless an application supplies a custom client factory.

## Experimental surface

Implementation-detail modules, CI scripts, research/evaluation code, proof harnesses, generated benchmark artifacts, and advanced protocol helpers may evolve in minor releases. Pin a package version if depending directly on them.

## Deprecation

Where practical, behavior that was previously public remains available for at least one migration window and emits a deprecation notice naming the preferred surface. Unsafe behavior may be changed without preserving an insecure compatibility path.
