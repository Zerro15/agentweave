from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

STRATEGIES = (
    "all_tools_dynamic",
    "pre_inference_routing",
    "hierarchical_delegation",
    "hybrid_bounded_dynamic",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    catalog_size: int = 10
    route_budget: int = 2
    emergent: bool = False
    routing_miss: bool = False
    tool_failure: bool = False
    malicious_permitted: bool = False
    unauthorized_injection: bool = False
    unnecessary_search: bool = False


SCENARIOS = (
    Scenario("obvious_request"),
    Scenario("equivalent_specialists"),
    Scenario("emergent_capability", route_budget=1, emergent=True),
    Scenario("routing_miss", route_budget=1, routing_miss=True),
    Scenario("tool_failure", route_budget=1, tool_failure=True),
    Scenario("noisy_catalog", catalog_size=50),
    Scenario("malicious_permitted", malicious_permitted=True, route_budget=1),
    Scenario("unauthorized_injection", unauthorized_injection=True),
    Scenario("unnecessary_search", unnecessary_search=True),
)


def _base_row(strategy: str, s: Scenario) -> dict:
    permitted = s.catalog_size - 1  # one policy-denied tool is present in every source catalog
    row = {
        "scenario": s.name,
        "strategy": strategy,
        "task_success": True,
        "recovery_success": False,
        "initial_visible_tools": permitted,
        "final_visible_tools": permitted,
        "model_calls": 1,
        "routing_calls": 0,
        "delegation_calls": 0,
        "search_calls": 0,
        "execution_calls": 1,
        "malicious_candidates_exposed": int(s.malicious_permitted),
        "unauthorized_selection_attempts": int(s.unauthorized_injection),
        "unauthorized_executions": 0,
        "unnecessary_delegations": 0,
        "unnecessary_searches": 0,
        "unnecessary_routings": 0,
        "failure_stage": "none",
        "provenance_complete": True,
    }
    if strategy == "pre_inference_routing":
        row["routing_calls"] = 1
        row["initial_visible_tools"] = min(s.route_budget, permitted)
        row["final_visible_tools"] = row["initial_visible_tools"]
        row["malicious_candidates_exposed"] = 0
        if s.routing_miss:
            row.update(task_success=False, failure_stage="routing", execution_calls=0)
        elif s.emergent:
            row.update(task_success=False, failure_stage="routing", model_calls=2)
        elif s.tool_failure:
            row.update(task_success=False, failure_stage="execution")
    elif strategy == "hierarchical_delegation":
        row["delegation_calls"] = 1
        row["model_calls"] = 2
        row["initial_visible_tools"] = min(5, permitted)
        row["final_visible_tools"] = row["initial_visible_tools"]
        if s.emergent:
            row["delegation_calls"] += 1
            row["model_calls"] += 2
        if s.tool_failure:
            row["delegation_calls"] += 1
            row["model_calls"] += 2
            row["execution_calls"] += 1
            row["recovery_success"] = True
        if s.name in {"obvious_request", "unnecessary_search"}:
            row["unnecessary_delegations"] = 1
    elif strategy == "hybrid_bounded_dynamic":
        row["routing_calls"] = 1
        row["initial_visible_tools"] = min(s.route_budget, permitted)
        row["final_visible_tools"] = row["initial_visible_tools"]
        row["malicious_candidates_exposed"] = 0
        if s.routing_miss or s.emergent or s.tool_failure:
            row["search_calls"] += 1
            row["routing_calls"] += 1
            row["model_calls"] += 1
            row["final_visible_tools"] = min(permitted, row["final_visible_tools"] + 1)
            row["recovery_success"] = True
            if s.tool_failure:
                row["execution_calls"] += 1
        if s.unnecessary_search:
            row["search_calls"] += 1
            row["routing_calls"] += 1
            row["unnecessary_searches"] = 1
            row["final_visible_tools"] = min(permitted, row["final_visible_tools"] + 1)
    else:  # all-tools dynamic
        if s.emergent:
            row["model_calls"] += 1
            row["execution_calls"] += 1
        if s.tool_failure:
            row["model_calls"] += 1
            row["execution_calls"] += 1
            row["recovery_success"] = True

    row["candidate_reduction"] = 1.0 - row["final_visible_tools"] / max(1, s.catalog_size)
    row["token_proxy"] = row["model_calls"] * (80 + 110 * max(1, row["final_visible_tools"])) + row["search_calls"] * 45
    row["routing_latency_ms_proxy"] = row["routing_calls"] * 2.0 + row["search_calls"] * 3.0
    row["model_latency_ms_proxy"] = row["model_calls"] * 25.0 + row["delegation_calls"] * 4.0
    row["execution_latency_ms_proxy"] = row["execution_calls"] * 5.0
    row["total_latency_ms_proxy"] = (
        row["routing_latency_ms_proxy"]
        + row["model_latency_ms_proxy"]
        + row["execution_latency_ms_proxy"]
    )
    return row


def run_benchmark() -> dict:
    rows = [_base_row(strategy, scenario) for scenario in SCENARIOS for strategy in STRATEGIES]
    aggregate = {}
    for strategy in STRATEGIES:
        selected = [row for row in rows if row["strategy"] == strategy]
        n = len(selected)
        aggregate[strategy] = {
            "scenarios": n,
            "task_success_rate": sum(row["task_success"] for row in selected) / n,
            "recovery_success_rate": sum(row["recovery_success"] for row in selected) / n,
            "mean_initial_visible_tools": sum(row["initial_visible_tools"] for row in selected) / n,
            "mean_final_visible_tools": sum(row["final_visible_tools"] for row in selected) / n,
            "mean_candidate_reduction": sum(row["candidate_reduction"] for row in selected) / n,
            "model_calls": sum(row["model_calls"] for row in selected),
            "routing_calls": sum(row["routing_calls"] for row in selected),
            "delegation_calls": sum(row["delegation_calls"] for row in selected),
            "search_calls": sum(row["search_calls"] for row in selected),
            "token_proxy": sum(row["token_proxy"] for row in selected),
            "total_latency_ms_proxy": sum(row["total_latency_ms_proxy"] for row in selected),
            "malicious_candidates_exposed": sum(row["malicious_candidates_exposed"] for row in selected),
            "unauthorized_selection_attempts": sum(row["unauthorized_selection_attempts"] for row in selected),
            "unauthorized_executions": sum(row["unauthorized_executions"] for row in selected),
            "provenance_complete_rate": sum(row["provenance_complete"] for row in selected) / n,
            "unnecessary_delegations": sum(row["unnecessary_delegations"] for row in selected),
            "unnecessary_searches": sum(row["unnecessary_searches"] for row in selected),
            "unnecessary_routings": sum(row["unnecessary_routings"] for row in selected),
        }
    return {
        "protocol": "issue-38-controlled-hybrid-selection-v1",
        "evidence_boundary": {
            "selection_policy": "deterministic controlled model proxy",
            "token_values": "modeled proxy",
            "latency_values": "modeled proxy",
            "production_claim": False,
            "frozen_historical_results_modified": False,
        },
        "scenarios": [asdict(scenario) for scenario in SCENARIOS],
        "aggregate": aggregate,
        "rows": rows,
    }


def main(path: str = "evaluation/hybrid-selection-issue38-v1.json") -> dict:
    payload = run_benchmark()
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


if __name__ == "__main__":
    main()
