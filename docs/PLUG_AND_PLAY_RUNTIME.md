# Plug-and-play AgentWeave runtime

`AgentWeaveRuntime` is the canonical runtime for tool-rich model applications.

## Security and execution pipeline

```text
request + RunContext
        ↓
CatalogProvider
        ↓
ScopePolicy
        ↓
Router
        ↓
small model-visible ToolSpec set
        ↓
ModelAdapter
        ↓
normalized ToolCall
        ↓
AuthorizationPolicy / AuthorizationGate
        ↓
Executor
        ↓
normalized ToolResult
        ↓
model continuation
        ↘ execution failure → bounded re-route / recovery
```

The runtime keeps three decisions separate:

1. **scope** decides which tools the caller may even expose to relevance routing;
2. **routing** decides which permitted tools are relevant enough to show the model;
3. **authorization** decides whether the model-selected action may execute now.

Routing therefore never grants execution permission.

## Normalized contracts

All integrations translate provider-native objects to a small core representation:

- `ToolSpec` — provider-neutral tool identity, description, input schema, risk and policy metadata;
- `ToolCall` — normalized selected action and arguments;
- `ToolResult` — normalized success/error and content;
- `ModelResponse` — normalized text, tool calls, usage and finish reason;
- `RunContext` — identity, role, tenant, permissions, scopes, environment and approval state.

Framework and protocol adapters should depend on these contracts or on `AgentWeaveRuntime`, not on the internal matcher/graph/optimizer modules.

## Deferred discovery

If routing is uncertain, an optional asynchronous search provider can discover more tools. Newly discovered candidates are treated as untrusted catalog additions:

```text
router abstains
    ↓
search provider
    ↓
new candidates
    ↓
ScopePolicy AGAIN
    ↓
re-route permitted candidates only
```

A discovered tool is never model-visible merely because a search provider returned it.

## Builder API

```python
from agentweave import AgentWeaveBuilder, StaticToolCatalog, CallableExecutor, ToolSpec
from agentweave_byom import OpenAICompatibleModelAdapter

model = OpenAICompatibleModelAdapter(
    model='my-model',
    base_url='http://localhost:8000/v1',
)

tools = [ToolSpec(name='lookup', description='Look up a customer')]

runtime = (
    AgentWeaveBuilder()
    .model(model)
    .catalog(StaticToolCatalog(tools))
    .executor(CallableExecutor({'lookup': lookup}))
    .options(max_tools=6, max_recovery_attempts=2)
    .build()
)

result = await runtime.run('Look up customer 123')
```

## Declarative configuration

```yaml
model:
  kind: openai-compatible
  model: my-model
  base_url: https://gateway.example/v1
  api_key_env: MODEL_API_KEY

catalog:
  kind: mcp
  target: https://tools.example/mcp

routing:
  kind: adaptive
  max_tools: 8
  min_confidence: 0.5
  expansion_factor: 2
  max_abstention_tools: 24
  search_limit: 16

limits:
  max_model_turns: 4
  max_recovery_attempts: 2
```

Run it with:

```bash
agentweave --config agentweave.yaml run 'Find the latest invoice'
```

## Plugin components

Plugins declare a compatible `api_version`, configure a `ComponentRegistry`, and may implement async start/stop lifecycle hooks. Plugins can register model, catalog, executor, router, policy, integration and telemetry components without importing runtime internals.

## Integrations

- MCP: `MCPToolCatalog` + `MCPExecutor`;
- LangGraph: `AgentWeaveLangGraphNode` / `langgraph_node`;
- AutoGen: `AgentWeaveAutoGenSelector`;
- custom providers: implement the small model/catalog/executor/router protocols.

## HTTP safety

AgentWeave-owned HTTP integrations share `SafeHttpTransport`, which applies endpoint validation, DNS pinning and rebinding checks, guarded redirects, Host/SNI preservation and cross-origin credential stripping. Protocol SDKs that own their own socket/session lifecycle are validated at the AgentWeave handoff boundary as well.
