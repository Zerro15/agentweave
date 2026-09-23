# Issue #38 — Dynamic vs pre-inference vs hierarchical vs hybrid selection

This document records the controlled experiment added for Issue #38. It compares who makes the tool-selection decision while keeping the task catalog and authorization contract matched across strategies.

## Strategies

1. **All-tools dynamic selection** — all policy-permitted tools remain model-visible and the model proxy selects dynamically.
2. **Pre-inference routing** — a fixed routed candidate set is chosen before selection and is not expanded later.
3. **Hierarchical delegation** — a top-level selector delegates into a specialist group and can delegate again when state changes.
4. **Hybrid bounded dynamic selection** — policy first, bounded routing second, dynamic selection inside the bounded pool, with deferred discovery/re-routing only on miss, state change, or failure.

The benchmark is implemented in `evaluation/hybrid_selection.py`. The frozen result artifact is `evaluation/hybrid-selection-issue38-v1.json`.

## Scenarios

The suite covers an obvious request, equivalent specialists, an emergent capability, an intentional routing miss, selected-tool failure, a noisy catalog, a policy-permitted malicious distractor, an injected unauthorized action, and an unnecessary deferred-search case.

Authorization is independent of routing in all conditions. The injected unauthorized action is denied before execution for every strategy.

## Controlled v1 results

| Strategy | Task success | Mean initial visible tools | Candidate reduction | Model calls | Routing calls | Search/delegation calls | Token proxy | Latency proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| All-tools dynamic | 100% | 13.44 | 9.1% | 11 | 0 | 0 | 16,170 | 330 ms |
| Fixed pre-routing | 66.7% | 1.56 | 86.2% | 10 | 9 | 0 | 2,450 | 308 ms |
| Hierarchical delegation | 100% | 5.00 | 54.4% | 22 | 0 | 11 delegations | 13,860 | 644 ms |
| Hybrid bounded dynamic | 100% | 1.56 | 81.8% final reduction | 12 | 13 | 4 searches | 3,780 | 388 ms |

The controlled result demonstrates the intended architectural trade-off: fixed pre-routing is cheapest in the proxy model but loses success when capabilities emerge after the initial decision, a routing miss occurs, or the initially selected tool fails. The hybrid condition restores those cases through bounded re-discovery while retaining substantially lower exposure than all-tools dynamic selection.

## Security result

Every strategy records one injected unauthorized-selection attempt in the adversarial scenario and **zero unauthorized executions**. Routing/search is never treated as authorization.

## Evidence boundary

This is a deterministic controlled architecture experiment, not an LLM-provider benchmark.

- Selection uses a deterministic model proxy so strategy mechanics can be compared without sampling variance.
- Token values are modeled proxies.
- Latency values are modeled proxies.
- The experiment does not modify BFCL-derived frozen results or historical router holdouts.
- The results must not be described as production performance or as evidence that the hybrid strategy universally outperforms dynamic agentic selection.

A provider-backed follow-up can reuse the same scenario and metric schema while replacing the deterministic selector with a real model adapter.
