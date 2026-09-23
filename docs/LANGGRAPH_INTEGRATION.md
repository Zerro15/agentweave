# LangGraph integration

AgentWeave provides a first-class async LangGraph routing node backed only by the public `AgentWeaveRuntime` API.

```text
LangGraph state
    ↓
AgentWeaveLangGraphNode
    ↓
AgentWeaveRuntime.preview_route()
    ↓
selected/permitted candidates + confidence + provenance
    ↓
normal LangGraph downstream nodes
```

## Install

```bash
pip install 'agentweave-router[langgraph]'
```

## Use as a graph node

```python
from langgraph.graph import START, END, StateGraph
from agentweave.integrations.langgraph import langgraph_node

runtime = ...  # AgentWeaveRuntime

graph = StateGraph(MyState)
graph.add_node('route', langgraph_node(runtime, query_key='query'))
graph.add_edge(START, 'route')
graph.add_edge('route', 'work')
graph.add_edge('work', END)
app = graph.compile()
```

The node returns:

- `agentweave_selected_tools`;
- `agentweave_permitted_tools`;
- `agentweave_routing_confidence`;
- `agentweave_routing_abstained`;
- `agentweave_routing_provenance`.

A `context_factory` can turn graph state into a `RunContext` for tenant, role, permission, scope, environment, and approval-aware routing.

## Public API boundary

The integration does not reach into `RequirementAnalyzer`, `AgentMatcher`, `GlobalTeamOptimizer`, registry internals, or observability internals. Those components may evolve without forcing LangGraph applications to change.

See [`examples/langgraph_agentweave.py`](../examples/langgraph_agentweave.py) for a local routing-only example.

## Evidence boundary

This is an ecosystem integration feature, not a benchmark result. It does not imply LangGraph/LangChain endorsement or modify frozen AgentWeave research evidence.
