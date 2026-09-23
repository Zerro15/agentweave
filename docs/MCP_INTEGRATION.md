# MCP integration

AgentWeave provides a first-class MCP runtime boundary rather than only descriptor routing.

```text
MCP server
   ↓
MCPConnection
   ↓
MCPToolCatalog.list_tools()
   ↓
ToolSpec normalization + optional restrictive policy metadata
   ↓
AgentWeave scope policy
   ↓
AgentWeave routing
   ↓
model-visible tools
   ↓
model ToolCall
   ↓
JSON Schema validation
   ↓
argument-aware authorization gate
   ↓
MCPExecutor.call_tool()
   ↓
ToolResult normalization
```

## Install

```bash
pip install 'agentweave-router[mcp]'
```

The Python import package remains `agentweave`.

## Shared session lifecycle

Catalog and executor can share one `MCPConnection`, which allows the canonical runtime to reuse one MCP client session instead of reconnecting for every list/call.

```python
from agentweave import AgentWeaveRuntime, RunContext
from agentweave.integrations.mcp import MCPConnection, MCPExecutor, MCPToolCatalog
from agentweave_byom import OpenAICompatibleModelAdapter

connection = MCPConnection('https://tools.example/mcp')

runtime = AgentWeaveRuntime(
    model=OpenAICompatibleModelAdapter(
        model='my-model',
        base_url='https://model.example/v1',
    ),
    catalog=MCPToolCatalog(connection=connection),
    executor=MCPExecutor(connection=connection),
)

async with runtime:
    result = await runtime.run(
        'Search the codebase for the routing implementation',
        context=RunContext(permissions=frozenset({'read'})),
    )
```

The runtime owns component `start()` / `stop()` lifecycle. `MCPConnection` is idempotent, so sharing it between catalog and executor does not open duplicate sessions.

## Tool identity

An MCP tool receives a stable identity containing its source and native tool name. Stable identity is separate from the function name shown to the model. If an aggregated catalog contains the same model-visible name from more than one source, AgentWeave fails loudly and requires explicit `ToolSpec.model_name` aliases rather than silently dropping one provider.

## Security ordering

MCP discovery does not grant permission to execute a tool. The canonical runtime ordering is:

1. discover the source catalog;
2. apply deterministic role / tenant / permission / scope filtering;
3. route only the permitted set;
4. expose the selected set to the model;
5. normalize the model tool call;
6. validate arguments against the selected tool's JSON Schema;
7. authorize the resolved tool identity and arguments immediately before execution;
8. execute with `MCPExecutor` only after an explicit allow;
9. reroute/recover on bounded execution failure.

Deferred discovery is re-screened through scope policy before newly discovered tools become model-visible.

## MCP policy metadata

AgentWeave recognizes an opt-in restrictive namespace under MCP tool metadata:

```json
{
  "agentweave": {
    "permissions": ["read"],
    "scopes": ["customer:read"],
    "roles": ["analyst"],
    "tenants": ["acme"],
    "environments": ["prod"],
    "risk_level": "high"
  }
}
```

These values can only add restrictions. MCP destructive annotations can elevate a tool to high risk. Untrusted metadata is not allowed to lower the runtime's default risk classification.

## HTTP trust boundary

For HTTP MCP targets, AgentWeave validates the endpoint before each MCP connection establishment. The MCP SDK owns its own protocol transport, so this is intentionally described as **endpoint validation + session lifecycle**, not as AgentWeave owning the SDK's wire transport.

AgentWeave-owned HTTP integrations use `SafeHttpTransport`, which applies endpoint validation, DNS pinning/rebinding checks, guarded redirects, Host/SNI preservation, and cross-origin credential stripping.

Applications that require complete wire-level transport control for MCP can provide a custom `client_factory` when constructing `MCPConnection`.

## Compatibility CI

A dedicated GitHub Actions matrix installs the real supported `mcp>=2,<3` package and verifies the upstream client surface in addition to unit tests that use deterministic fakes. This separates upstream SDK drift from core runtime regressions.

## Routing-only example

[`examples/mcp_tool_routing.py`](../examples/mcp_tool_routing.py) connects to a real MCP server, lists tools through `MCPToolCatalog`, and runs the production adaptive router. It does not import the historical BFCL benchmark proxy.

## Evidence boundary

MCP support is a runtime/integration feature. It does not modify frozen BFCL-derived artifacts and should not be described as official MCP endorsement or benchmark evidence.
