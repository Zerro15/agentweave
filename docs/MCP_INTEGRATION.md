# MCP integration

AgentWeave now provides a first-class MCP runtime boundary rather than only a descriptor-routing example.

```text
MCP server
   ↓
MCPToolCatalog.list_tools()
   ↓
ToolSpec normalization
   ↓
AgentWeave scope policy
   ↓
AgentWeave routing
   ↓
model-visible tools
   ↓
model ToolCall
   ↓
authorization gate
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

## Catalog and executor

```python
from agentweave import AgentWeaveRuntime, RunContext
from agentweave.integrations.mcp import MCPExecutor, MCPToolCatalog
from agentweave_byom import OpenAICompatibleModelAdapter

url = 'http://localhost:8000/mcp'
model = OpenAICompatibleModelAdapter(
    model='my-model',
    base_url='http://localhost:8001/v1',
)

runtime = AgentWeaveRuntime(
    model=model,
    catalog=MCPToolCatalog(url),
    executor=MCPExecutor(url),
)

result = await runtime.run(
    'Search the codebase for the routing implementation',
    context=RunContext(),
)
```

`MCPToolCatalog` uses the MCP client contract to list and paginate tools and normalizes them to `ToolSpec`. `MCPExecutor` executes normalized `ToolCall` values through the MCP client and converts results to `ToolResult`.

## Security ordering

MCP discovery does not grant permission to execute a tool. The canonical runtime ordering is:

1. discover the source catalog;
2. apply deterministic role / tenant / permission / scope filtering;
3. route only the permitted set;
4. expose the selected set to the model;
5. normalize the model tool call;
6. authorize the selected action again immediately before execution;
7. execute with `MCPExecutor` only after an explicit allow;
8. reroute/recover on bounded execution failure.

Deferred discovery is also re-screened through scope policy before any newly discovered tool can become model-visible.

## Routing-only example

[`examples/mcp_tool_routing.py`](../examples/mcp_tool_routing.py) connects to a real MCP server, lists its tools through `MCPToolCatalog`, and runs the production adaptive router. It no longer imports the BFCL benchmark routing proxy.

```bash
python examples/mcp_tool_routing.py \
  --url http://localhost:8000/mcp \
  --query 'Open an issue about routing provenance'
```

## HTTP trust boundary

For HTTP MCP targets, AgentWeave validates the target before handing the session to the MCP SDK. AgentWeave-owned HTTP integrations use `SafeHttpTransport`, which applies endpoint validation, DNS pinning/rebinding checks, guarded redirects, Host/SNI preservation, and cross-origin credential stripping.

## Evidence boundary

MCP support is a runtime/integration feature. It does not modify frozen BFCL-derived artifacts and should not be described as official MCP endorsement or benchmark evidence.
