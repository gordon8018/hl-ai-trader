from datetime import datetime, timezone

import pytest

from shared.v17.schemas import V17FeatureSnapshot
from services.v17_data_builder.features import (
    InsufficientFeatureHistoryError,
    build_feature_snapshot,
)
from services.v17_data_builder.ohlcv_cache import OHLCVCacheReader


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def row(
    symbol: str,
    timestamp: str,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def deterministic_rows(symbol: str = "BTC") -> list[dict[str, object]]:
    return [
        row(symbol, "2026-06-13T00:00:00Z", open_=100, high=103, low=99, close=101, volume=100),
        row(symbol, "2026-06-13T01:00:00Z", open_=101, high=104, low=100, close=102, volume=120),
        row(symbol, "2026-06-13T02:00:00Z", open_=102, high=106, low=101, close=105, volume=110),
        row(symbol, "2026-06-13T03:00:00Z", open_=105, high=107, low=104, close=104, volume=160),
        row(symbol, "2026-06-13T04:00:00Z", open_=104, high=110, low=103, close=108, volume=140),
        row(symbol, "2026-06-13T05:00:00Z", open_=108, high=112, low=107, close=111, volume=200),
    ]


def read_result(rows: list[dict[str, object]], as_of: str = "2026-06-13T06:01:00Z"):
    reader = OHLCVCacheReader(rows, freshness_tolerance_seconds=7200)
    return reader.read_symbol("BTC", as_of=utc(as_of), min_history=1)


def test_builds_v17_feature_snapshot_with_expected_atr_and_ranks() -> None:
    result = build_feature_snapshot(read_result(deterministic_rows()), side="LONG")
    snapshot = result.to_snapshot()

    assert isinstance(snapshot, V17FeatureSnapshot)
    assert snapshot.symbol == "BTC"
    assert snapshot.bar_close_time_utc == "2026-06-13T05:00:00Z"
    assert snapshot.entry_time_utc == "2026-06-13T06:00:00Z"
    assert snapshot.side == "LONG"
    assert snapshot.status == "valid"
    assert snapshot.reason == "quality_inputs_ready"
    assert snapshot.features["atr"] == pytest.approx(4.8)
    assert snapshot.features["atr_pct"] == pytest.approx(4.8 / 111)
    assert snapshot.features["bar_range"] == pytest.approx(5.0)
    assert snapshot.features["bar_range_pct"] == pytest.approx(5.0 / 111)
    assert snapshot.features["range_rank"] == pytest.approx(0.6)
    assert snapshot.features["volume"] == pytest.approx(200.0)
    assert snapshot.features["volume_rank"] == pytest.approx(0.9)
    assert snapshot.features["momentum_1h"] == pytest.approx(3 / 108)
    assert snapshot.features["side_momentum_1h"] == pytest.approx(3 / 108)
    assert snapshot.features["side_momentum_rank"] == pytest.approx(0.5)
    assert V17FeatureSnapshot.from_dict(snapshot.to_dict()).to_dict() == snapshot.to_dict()


def test_side_aware_momentum_rank_inverts_for_short_side() -> None:
    long_snapshot = build_feature_snapshot(read_result(deterministic_rows()), side="LONG").to_snapshot()
    short_snapshot = build_feature_snapshot(read_result(deterministic_rows()), side="SHORT").to_snapshot()

    assert short_snapshot.features["side_momentum_1h"] == pytest.approx(
        -long_snapshot.features["side_momentum_1h"]
    )
    assert long_snapshot.features["side_momentum_rank"] == pytest.approx(0.5)
    assert short_snapshot.features["side_momentum_rank"] == pytest.approx(0.5)


def test_insufficient_history_returns_invalid_or_raises_clear_error() -> None:
    sparse = read_result(deterministic_rows()[:5], as_of="2026-06-13T05:01:00Z")

    result = build_feature_snapshot(sparse, side="LONG")

    assert result.status == "invalid"
    assert result.reason == "insufficient_history"
    assert result.to_snapshot().features == {}
    with pytest.raises(InsufficientFeatureHistoryError, match="insufficient_history"):
        build_feature_snapshot(sparse, side="LONG", raise_on_invalid=True)


def test_future_bar_changes_do_not_affect_current_features() -> None:
    base_rows = deterministic_rows()
    with_future = base_rows + [
        row("BTC", "2026-06-13T06:00:00Z", open_=999, high=1500, low=1, close=777, volume=999999)
    ]
    mutated_future = base_rows + [
        row("BTC", "2026-06-13T06:00:00Z", open_=1, high=2, low=1, close=1, volume=1)
    ]

    first = build_feature_snapshot(
        read_result(with_future, as_of="2026-06-13T06:30:00Z"), side="LONG"
    ).to_dict()
    second = build_feature_snapshot(
        read_result(mutated_future, as_of="2026-06-13T06:30:00Z"), side="LONG"
    ).to_dict()

    assert first == second
    assert first["bar_close_time_utc"] == "2026-06-13T05:00:00Z"


def test_accepts_t03_cache_rows_and_ignores_non_requested_symbol() -> None:
    rows = deterministic_rows("btc") + deterministic_rows("ETH")
    eth_result = OHLCVCacheReader(rows, freshness_tolerance_seconds=7200).read_symbol(
        "eth",
        as_of=utc("2026-06-13T06:01:00Z"),
    )

    snapshot = build_feature_snapshot(eth_result, side="FLAT").to_snapshot()

    assert snapshot.symbol == "ETH"
    assert snapshot.status == "valid"
    assert snapshot.side == "FLAT"
    assert snapshot.features["side_momentum_1h"] == pytest.approx(0.0)
    assert snapshot.features["side_momentum_rank"] == pytest.approx(0.5)


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

    snapshot = build_feature_snapshot(read_result(deterministic_rows()), side="LONG").to_snapshot()

    assert blocked_imports == []
    assert snapshot.status == "valid"
