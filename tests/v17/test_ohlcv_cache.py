from datetime import datetime, timezone

import pytest

from services.v17_data_builder.ohlcv_cache import (
    InsufficientHistoryError,
    MissingSymbolError,
    OHLCVCacheReader,
    load_freshness_tolerance_seconds,
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def row(symbol: str, timestamp: str, close: float = 100.0) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": 1234.5,
    }


def test_filters_to_completed_1h_bars_only() -> None:
    reader = OHLCVCacheReader(
        [
            row("btc", "2026-06-13T15:00:00Z", close=101.0),
            row("btc", "2026-06-13T16:00:00Z", close=102.0),
            row("btc", "2026-06-13T17:00:00Z", close=103.0),
        ],
        freshness_tolerance_seconds=7200,
    )

    result = reader.read_symbol("BTC", as_of=utc("2026-06-13T17:30:00Z"))

    assert result.status == "valid"
    assert [bar.timestamp for bar in result.bars] == [
        utc("2026-06-13T15:00:00Z"),
        utc("2026-06-13T16:00:00Z"),
    ]
    assert result.latest_bar is not None
    latest_payload = result.latest_bar.to_dict()
    assert latest_payload["symbol"] == "BTC"
    assert latest_payload["timestamp"] == "2026-06-13T16:00:00Z"
    assert latest_payload["open"] == 101.0
    assert latest_payload["high"] == 104.0
    assert latest_payload["low"] == 100.0
    assert latest_payload["close"] == 102.0
    assert latest_payload["volume"] == 1234.5


def test_rejects_stale_completed_bar_using_config_tolerance() -> None:
    tolerance = load_freshness_tolerance_seconds()
    reader = OHLCVCacheReader(
        [row("ETH", "2026-06-13T12:00:00Z")],
        freshness_tolerance_seconds=tolerance,
    )

    result = reader.read_symbol("ETH", as_of=utc("2026-06-13T14:00:01Z"))

    assert tolerance == 5400
    assert result.status == "invalid"
    assert result.reason == "stale_bar"
    assert result.latest_bar is not None
    assert result.latest_bar.symbol == "ETH"


def test_missing_symbol_returns_invalid_or_raises() -> None:
    reader = OHLCVCacheReader([row("BTC", "2026-06-13T15:00:00Z")])

    result = reader.read_symbol("SOL", as_of=utc("2026-06-13T16:05:00Z"))

    assert result.status == "invalid"
    assert result.reason == "missing_symbol"
    with pytest.raises(MissingSymbolError):
        reader.read_symbol(
            "SOL",
            as_of=utc("2026-06-13T16:05:00Z"),
            raise_on_invalid=True,
        )


def test_insufficient_history_returns_invalid_or_raises() -> None:
    reader = OHLCVCacheReader(
        [row("BTC", "2026-06-13T15:00:00Z")],
        freshness_tolerance_seconds=7200,
    )

    result = reader.read_symbol(
        "BTC",
        as_of=utc("2026-06-13T16:05:00Z"),
        min_history=2,
    )

    assert result.status == "invalid"
    assert result.reason == "insufficient_history"
    with pytest.raises(InsufficientHistoryError):
        reader.read_symbol(
            "BTC",
            as_of=utc("2026-06-13T16:05:00Z"),
            min_history=2,
            raise_on_invalid=True,
        )


def test_loads_csv_without_live_market_data_or_execution_paths(tmp_path, monkeypatch) -> None:
    blocked_imports: list[str] = []
    original_import = __import__

    def guard_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(
            (
                "redis",
                "services.market_data",
                "services.execution",
                "services.risk_engine",
            )
        ):
            blocked_imports.append(name)
            raise AssertionError(f"unexpected live-path import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guard_import)
    csv_path = tmp_path / "ohlcv.csv"
    csv_path.write_text(
        "symbol,timestamp,open,high,low,close,volume\n"
        "BTC,2026-06-13T15:00:00Z,99,103,98,101,1200\n"
    )

    reader = OHLCVCacheReader.from_csv(csv_path, freshness_tolerance_seconds=7200)
    result = reader.read_symbol("BTC", as_of=utc("2026-06-13T16:01:00Z"))

    assert blocked_imports == []
    assert result.status == "valid"
    assert result.latest_bar is not None
    assert result.latest_bar.close == 101.0
