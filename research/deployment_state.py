from __future__ import annotations

VALID_TRANSITIONS = {
    "research_candidate": {"shadow_running"},
    "shadow_running": {"canary_live", "rollback"},
    "canary_live": {"stable", "rollback"},
    "rollback": {"stable"},
    "stable": set(),
}


def can_transition(src: str, dst: str) -> bool:
    return dst in VALID_TRANSITIONS.get(src, set())
