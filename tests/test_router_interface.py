from agentweave_byom import (
    AdaptiveRouter,
    ConfidencePolicy,
    DeterministicRouterV1,
    Router,
    ToolRouter,
)


def _tool(name, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_deterministic_router_v1_is_formal_router_and_keeps_legacy_alias():
    router = DeterministicRouterV1()
    assert isinstance(router, Router)
    assert ToolRouter is DeterministicRouterV1
    result = router.route(
        "send an email",
        [_tool("send_email", "send an email message"), _tool("weather", "weather forecast")],
        max_tools=1,
    )
    assert result.selected[0]["function"]["name"] == "send_email"
    assert result.provenance["router"] == "agentweave-tool-router-v1"


def test_adaptive_router_abstains_by_expanding_uncertain_candidate_pool():
    tools = [_tool(f"tool_{i}", f"unrelated capability {i}") for i in range(8)]
    router = AdaptiveRouter(
        DeterministicRouterV1(),
        confidence_policy=ConfidencePolicy(
            min_confidence=0.50,
            expansion_factor=2,
            max_abstention_tools=6,
        ),
    )
    result = router.route("quantum resonance lookup", tools, max_tools=2)
    assert result.abstained is True
    assert len(result.selected) == 4
    assert result.provenance["adaptive_decision"] == "abstain-expand"
    assert result.provenance["confidence_is_probability"] is False


def test_adaptive_router_can_search_on_uncertainty():
    class Search:
        def search(self, text, *, excluded_names, limit):
            assert "quantum" in text
            return [_tool("quantum_lookup", "quantum resonance lookup")]

    router = AdaptiveRouter(
        DeterministicRouterV1(),
        search_provider=Search(),
    )
    result = router.route(
        "quantum resonance lookup",
        [_tool("weather", "weather forecast"), _tool("email", "send email")],
        max_tools=1,
    )
    names = [tool["function"]["name"] for tool in result.selected]
    assert result.abstained is True
    assert result.provenance["search_invoked"] is True
    assert "quantum_lookup" in names
