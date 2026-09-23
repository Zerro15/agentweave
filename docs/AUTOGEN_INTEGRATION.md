# AutoGen integration

AgentWeave provides a first-class AutoGen participant selector backed only by the public `AgentWeaveRuntime` API.

```text
AutoGen task/message
    ↓
AgentWeaveAutoGenSelector
    ↓
AgentWeaveRuntime.preview_route()
    ↓
small participant set
    ↓
normal AutoGen team execution
```

## Install

```bash
pip install 'agentweave-router[autogen]'
```

## Select participants

```python
from agentweave.integrations.autogen import AgentWeaveAutoGenSelector

runtime = ...  # AgentWeaveRuntime
participants = {
    'backend_specialist': 'Reviews backend services and APIs',
    'database_specialist': 'Reviews schemas, SQL, and database design',
    'research_specialist': 'Researches evidence and summarizes findings',
}

selector = AgentWeaveAutoGenSelector(
    runtime,
    participants,
    max_participants=2,
)
selected_names = await selector.select(task)
selected_agents = [all_agents[name] for name in selected_names]
```

`selector.selector_func(messages)` is also available as an async AutoGen-style callback that returns the next selected participant name.

## Public API boundary

The adapter converts participant descriptions into normalized `ToolSpec` candidates and calls `AgentWeaveRuntime.preview_route()`. It does not construct `RequirementAnalyzer`, `TrustEngine`, `AgentMatcher`, `GlobalTeamOptimizer`, or selection-explainer internals itself.

See [`examples/autogen_agentweave.py`](../examples/autogen_agentweave.py) for a keyless end-to-end example that routes participants before building an AutoGen `RoundRobinGroupChat`.

## Evidence boundary

This is an ecosystem integration feature, not a benchmark result. It does not modify AutoGen, imply Microsoft/AutoGen endorsement, or alter frozen AgentWeave research evidence.
