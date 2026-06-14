from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from services.v17_data_builder.ohlcv_cache import OHLCVBar, OHLCVReadResult
from services.v17_signal_engine.event_aggregator import build_candidate_signal


BASE_TIME = datetime(2026, 6, 13, 0, 0, tzinfo=timezone.utc)


def bar(index: int, *, symbol: str = "BTC", close: float | None = None) -> OHLCVBar:
    resolved_close = close if close is not None else 100.0 + index * 2.0
    return OHLCVBar(
        timestamp=BASE_TIME + timedelta(hours=index),
        open=resolved_close - 1.0,
        high=resolved_close + 3.0,
        low=resolved_close - 2.0,
        close=resolved_close,
        volume=1000.0 + index * 100.0,
        symbol=symbol,
    )


def read_result(symbol: str = "BTC", *, count: int = 6, status: str = "valid", reason: str = "") -> OHLCVReadResult:
    return OHLCVReadResult(
        symbol=symbol,
        bars=tuple(bar(index, symbol=symbol) for index in range(count)),
        status=status,  # type: ignore[arg-type]
        reason=reason,
    )


def test_happy_path_long_candidate_contains_shadow_event_fields() -> None:
    result = build_candidate_signal(read_result("btc"), side="LONG")
    event = result.to_event()

    assert result.status == "valid"
    assert event.strategy_version == "V17_shadow"
    assert event.event_id == "v17-shadow:BTC:LONG:2026-06-13T05:00:00Z:2026-06-13T06:00:00Z"
    assert event.entry_time_utc == "2026-06-13T06:00:00Z"
    assert event.bar_close_time_utc == "2026-06-13T05:00:00Z"
    assert event.event_gross_cap == pytest.approx(0.06)
    assert len(event.legs) == 1
    leg = event.legs[0]
    assert leg.symbol == "BTC"
    assert leg.side == "LONG"
    assert leg.quality_score == pytest.approx(0.506818)
    assert leg.quality_label == "Q3"
    assert leg.raw_leg_weight == pytest.approx(0.03)
    assert leg.final_leg_weight == pytest.approx(0.03)
    assert "cap_multiplier=0.500000" in leg.reason


def test_happy_path_short_candidate_uses_negative_final_weight() -> None:
    result = build_candidate_signal(read_result("ETH"), side="SHORT")
    leg = result.to_event().legs[0]

    assert result.status == "valid"
    assert leg.symbol == "ETH"
    assert leg.side == "SHORT"
    assert leg.raw_leg_weight == pytest.approx(0.045)
    assert leg.final_leg_weight == pytest.approx(-0.045)


def test_invalid_feature_handling_returns_explicit_invalid_event() -> None:
    result = build_candidate_signal(read_result(count=3), side="LONG")
    event = result.to_event()

    assert result.status == "invalid"
    assert event.reason == "insufficient_history"
    assert event.legs[0].status == "invalid"
    assert event.legs[0].quality_score is None
    assert event.legs[0].quality_label == "UNAVAILABLE"
    assert event.legs[0].raw_leg_weight == 0.0
    assert event.legs[0].final_leg_weight == 0.0


def test_unavailable_score_handling_is_not_silent(monkeypatch) -> None:
    from services.v17_signal_engine import event_aggregator
    from services.v17_signal_engine.quality import QualityScoreResult

    def unavailable_score(snapshot):
        return QualityScoreResult(
            quality_score=None,
            quality_label="UNAVAILABLE",
            event_gross_cap_multiplier=0.0,
            status="invalid",
            reason="missing_quality_fields:volume_rank",
        )

    monkeypatch.setattr(event_aggregator, "score_quality", unavailable_score)

    result = build_candidate_signal(read_result(), side="LONG")

    assert result.status == "invalid"
    assert result.reason == "missing_quality_fields:volume_rank"
    assert result.to_event().legs[0].reason == "missing_quality_fields:volume_rank"


def test_stale_and_insufficient_data_reasons_propagate() -> None:
    stale = build_candidate_signal(read_result(status="invalid", reason="stale_bar"), side="LONG")
    insufficient = build_candidate_signal(
        OHLCVReadResult(symbol="SOL", bars=(), status="invalid", reason="insufficient_history"),
        side="SHORT",
    )

    assert stale.status == "invalid"
    assert stale.reason == "stale_bar"
    assert stale.to_event().legs[0].reason == "stale_bar"
    assert insufficient.status == "invalid"
    assert insufficient.reason == "insufficient_history"
    assert insufficient.to_event().legs[0].symbol == "SOL"
    assert insufficient.to_event().legs[0].side == "SHORT"


def test_no_production_side_effect_imports_or_target_conversion(monkeypatch) -> None:
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

    before_modules = set(sys.modules)
    event = build_candidate_signal(read_result(), side="LONG").to_event()
    after_modules = set(sys.modules)

    assert blocked_imports == []
    assert event.status == "valid"
    assert "services.execution" not in after_modules - before_modules
    assert "services.risk_engine" not in after_modules - before_modules


def test_deterministic_output_for_same_completed_bars() -> None:
    source = read_result("BTC")

    first = build_candidate_signal(source, side="LONG").to_dict()
    second = build_candidate_signal(source, side="LONG").to_dict()

    assert first == second
