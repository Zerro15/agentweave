from evaluation.hybrid_selection import run_benchmark


def test_issue_38_controlled_hybrid_benchmark_preserves_tradeoffs():
    payload = run_benchmark()
    aggregate = payload["aggregate"]

    assert aggregate["all_tools_dynamic"]["task_success_rate"] == 1.0
    assert aggregate["hybrid_bounded_dynamic"]["task_success_rate"] == 1.0
    assert aggregate["pre_inference_routing"]["task_success_rate"] < 1.0
    assert (
        aggregate["hybrid_bounded_dynamic"]["mean_initial_visible_tools"]
        < aggregate["all_tools_dynamic"]["mean_initial_visible_tools"]
    )
    assert aggregate["hybrid_bounded_dynamic"]["search_calls"] > 0
    assert aggregate["hierarchical_delegation"]["delegation_calls"] > 0
    assert aggregate["hybrid_bounded_dynamic"]["unauthorized_executions"] == 0
    assert aggregate["all_tools_dynamic"]["unauthorized_executions"] == 0
    assert aggregate["hybrid_bounded_dynamic"]["provenance_complete_rate"] == 1.0
    assert aggregate["hybrid_bounded_dynamic"]["token_proxy"] < aggregate["all_tools_dynamic"]["token_proxy"]


def test_issue_38_evidence_boundary_is_explicit():
    boundary = run_benchmark()["evidence_boundary"]
    assert boundary["selection_policy"] == "deterministic controlled model proxy"
    assert boundary["token_values"] == "modeled proxy"
    assert boundary["latency_values"] == "modeled proxy"
    assert boundary["production_claim"] is False
