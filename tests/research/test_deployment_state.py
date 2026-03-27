from research.deployment_state import can_transition


def test_can_transition_shadow_to_canary():
    assert can_transition("shadow_running", "canary_live") is True


def test_cannot_transition_candidate_to_stable():
    assert can_transition("research_candidate", "stable") is False
