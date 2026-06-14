from __future__ import annotations

import pytest

from shared.v17.schemas import V17FeatureSnapshot
from services.v17_signal_engine.quality import QualityScoreResult, score_quality


BASE_SNAPSHOT = {
    "symbol": "BTC",
    "bar_close_time_utc": "2026-06-13T05:00:00Z",
    "entry_time_utc": "2026-06-13T06:00:00Z",
    "side": "LONG",
    "status": "valid",
    "reason": "quality_inputs_ready",
    "features": {
        "range_rank": 0.5,
        "volume_rank": 0.5,
        "side_momentum_rank": 0.5,
        "atr_pct": 0.04,
    },
}


def snapshot_with_score(score: float) -> dict[str, object]:
    payload = dict(BASE_SNAPSHOT)
    payload["features"] = {
        "range_rank": score,
        "volume_rank": score,
        "side_momentum_rank": score,
        "atr_pct": score * 0.08,
    }
    return payload


def test_quality_score_threshold_boundaries_use_frozen_defaults() -> None:
    cases = [
        (0.75, "Q1"),
        (0.55, "Q2"),
        (0.35, "Q3"),
        (0.349999, "Q4"),
    ]

    for score, expected_label in cases:
        result = score_quality(snapshot_with_score(score))

        assert result.status == "valid"
        assert result.quality_score == pytest.approx(round(score, 6))
        assert result.quality_label == expected_label


def test_quality_score_accepts_v17_snapshot_dict_and_object_inputs() -> None:
    pydantic_snapshot = V17FeatureSnapshot.from_dict(BASE_SNAPSHOT)

    class CompatibleSnapshot:
        def __init__(self, payload: dict[str, object]) -> None:
            self.symbol = payload["symbol"]
            self.bar_close_time_utc = payload["bar_close_time_utc"]
            self.entry_time_utc = payload["entry_time_utc"]
            self.side = payload["side"]
            self.features = payload["features"]
            self.status = payload["status"]
            self.reason = payload["reason"]

    first = score_quality(pydantic_snapshot).to_dict()
    second = score_quality(dict(BASE_SNAPSHOT)).to_dict()
    third = score_quality(CompatibleSnapshot(dict(BASE_SNAPSHOT))).to_dict()

    assert first == second == third
    assert first["quality_score"] == pytest.approx(0.5)
    assert first["quality_label"] == "Q3"


def test_missing_required_fields_return_unavailable_without_raise() -> None:
    payload = dict(BASE_SNAPSHOT)
    payload["features"] = {
        "range_rank": 0.8,
        "volume_rank": 0.8,
        "atr_pct": 0.03,
    }

    result = score_quality(payload)

    assert result == QualityScoreResult(
        quality_score=None,
        quality_label="UNAVAILABLE",
        event_gross_cap_multiplier=0.0,
        status="invalid",
        reason="missing_quality_fields:side_momentum_rank",
    )


def test_invalid_snapshot_status_returns_unavailable_reason() -> None:
    payload = dict(BASE_SNAPSHOT)
    payload["status"] = "invalid"
    payload["reason"] = "insufficient_history"

    result = score_quality(payload)

    assert result.quality_score is None
    assert result.quality_label == "UNAVAILABLE"
    assert result.event_gross_cap_multiplier == 0.0
    assert result.status == "invalid"
    assert result.reason == "insufficient_history"


def test_structurally_wrong_input_raises_type_error() -> None:
    with pytest.raises(TypeError, match="snapshot must be"):
        score_quality(123)


def test_stable_output_and_clamping_are_deterministic() -> None:
    payload = dict(BASE_SNAPSHOT)
    payload["features"] = {
        "range_rank": 2.0,
        "volume_rank": -1.0,
        "side_momentum_rank": 0.5,
        "atr_pct": 0.16,
    }

    first = score_quality(payload).to_dict()
    second = score_quality(payload).to_dict()

    assert first == second
    assert first["quality_score"] == pytest.approx(0.55)
    assert first["quality_label"] == "Q2"


def test_label_to_cap_mapping_uses_config_overrides() -> None:
    config = {
        "quality_score": {
            "thresholds": {"Q1": 0.90, "Q2": 0.70, "Q3": 0.40},
            "label_cap_multipliers": {
                "Q1": 0.8,
                "Q2": 0.4,
                "Q3": 0.2,
                "Q4": 0.1,
                "UNAVAILABLE": 0.0,
            },
        }
    }

    result = score_quality(snapshot_with_score(0.7), config=config)

    assert result.quality_score == pytest.approx(0.7)
    assert result.quality_label == "Q2"
    assert result.event_gross_cap_multiplier == pytest.approx(0.4)


def test_does_not_import_live_market_execution_risk_or_llm_paths(monkeypatch) -> None:
    blocked_imports: list[str] = []
    original_import = __import__

    def guard_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(
            (
                "redis",
                "services.market_data",
                "services.execution",
                "services.risk_engine",
                "services.ai_decision",
                "openai",
                "anthropic",
            )
        ):
            blocked_imports.append(name)
            raise AssertionError(f"unexpected live-path import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guard_import)

    result = score_quality(BASE_SNAPSHOT)

    assert blocked_imports == []
    assert result.status == "valid"
