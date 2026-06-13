from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest

from services.v17_data_builder.ohlcv_cache import OHLCVBar, OHLCVReadResult
from services.v17_shadow import runner as shadow_runner
from services.v17_shadow.runner import V17ShadowRunner, run_shadow_once, validate_shadow_config
from shared.v17.schemas import V17SignalEvent, V17SignalLeg


BASE_TIME = datetime(2026, 6, 13, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeReader:
    status: str = "valid"
    reason: str = ""
    count: int = 6

    def read_symbol(self, symbol: str, *, as_of: datetime, min_history: int, raise_on_invalid: bool = False) -> OHLCVReadResult:
        if self.status != "valid":
            return OHLCVReadResult(symbol=symbol.upper(), bars=(), status="invalid", reason=self.reason)
        bars = tuple(
            OHLCVBar(
                timestamp=BASE_TIME + timedelta(hours=index),
                open=100.0 + index,
                high=103.0 + index,
                low=98.0 + index,
                close=102.0 + index * 2.0,
                volume=1000.0 + index * 100.0,
                symbol=symbol.upper(),
            )
            for index in range(self.count)
        )
        return OHLCVReadResult(symbol=symbol.upper(), bars=bars, status="valid")


class FakeLedger:
    def __init__(self, *, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.events = []

    def append_event(self, event):
        self.events.append(event)
        from services.v17_shadow.ledger import LedgerAppendResult

        return [
            LedgerAppendResult(
                row={"event_id": leg.event_id, "leg_id": leg.leg_id, "status": "duplicate" if self.duplicate else leg.status},
                status="duplicate" if self.duplicate else leg.status,
                duplicate=self.duplicate,
            )
            for leg in event.legs
        ]


class FakeSignalWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def write(self, stream: str, payload: Mapping[str, Any]) -> None:
        self.calls.append((stream, payload))


def config(**overrides) -> dict[str, Any]:
    base = {
        "enabled": True,
        "mode": "paper_shadow",
        "dry_run": True,
        "real_money_execution_allowed": False,
        "ohlcv_cache_path": "data/v17_shadow/ohlcv.csv",
        "streams": {
            "features_1h": "v17.features.1h",
            "signals": "v17.signals",
            "shadow_ledger": "v17.shadow.ledger",
            "target": "alpha.target.v17_shadow",
        },
        "ledger": {"path": "data/v17_shadow/ledger.csv"},
        "caps": {"max_event_gross": 0.06},
        "freshness": {"max_completed_bar_lag_seconds": 7200},
    }
    base.update(overrides)
    return base


def test_runner_aggregates_multi_symbol_event_and_writes_only_v17_stream() -> None:
    ledger = FakeLedger()
    writer = FakeSignalWriter()

    result = run_shadow_once(
        symbols=["btc", "eth"],
        as_of="2026-06-13T06:00:00Z",
        sides={"BTC": "LONG", "ETH": "SHORT"},
        config=config(),
        ohlcv_reader=FakeReader(),
        ledger=ledger,
        signal_writer=writer,
    )

    assert result.status == "valid"
    assert result.processed_count == 1
    assert result.valid_count == 1
    assert len(ledger.events) == 1
    event = ledger.events[0]
    assert event.event_id == "v17-shadow-event:V17_shadow:2026-06-13T05:00:00Z:2026-06-13T06:00:00Z:BTC+ETH"
    assert len(event.legs) == 2
    assert sum(abs(leg.final_leg_weight) for leg in event.legs) <= 0.060000000001
    assert [call[0] for call in writer.calls] == ["v17.signals"]
    assert writer.calls[0][1]["event_id"] == event.event_id


def test_runner_blocks_when_config_disabled_without_side_effects() -> None:
    ledger = FakeLedger()
    writer = FakeSignalWriter()

    result = run_shadow_once(
        symbols=["BTC"],
        config=config(enabled=False),
        ohlcv_reader=FakeReader(),
        ledger=ledger,
        signal_writer=writer,
    )

    assert result.status == "blocked"
    assert result.reason == "blocked_by_config:v17_shadow_disabled"
    assert result.blocked_count == 1
    assert ledger.events == []
    assert writer.calls == []


def test_runner_emits_invalid_skip_reasons_for_data_problems() -> None:
    ledger = FakeLedger()
    writer = FakeSignalWriter()

    result = run_shadow_once(
        symbols=["BTC"],
        as_of="2026-06-13T06:00:00Z",
        config=config(),
        ohlcv_reader=FakeReader(status="invalid", reason="stale_bar"),
        ledger=ledger,
        signal_writer=writer,
    )

    assert result.status == "invalid"
    assert result.invalid_count == 1
    assert result.events[0].reason == "stale_bar"
    assert ledger.events[0].status == "invalid"
    assert writer.calls[0][0] == "v17.signals"


def test_runner_surfaces_insufficient_history_skip_reason() -> None:
    ledger = FakeLedger()
    writer = FakeSignalWriter()

    result = run_shadow_once(
        symbols=["BTC"],
        as_of="2026-06-13T06:00:00Z",
        config=config(),
        ohlcv_reader=FakeReader(status="invalid", reason="insufficient_history"),
        ledger=ledger,
        signal_writer=writer,
    )

    assert result.status == "invalid"
    assert result.invalid_count == 1
    assert result.events[0].reason == "insufficient_history"
    assert ledger.events[0].reason == "insufficient_history"
    assert writer.calls[0][0] == "v17.signals"


def test_runner_surfaces_missing_score_skip_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = FakeLedger()
    writer = FakeSignalWriter()
    event = V17SignalEvent(
        event_id="v17-shadow:BTC:LONG:2026-06-13T05:00:00Z:2026-06-13T06:00:00Z",
        entry_time_utc="2026-06-13T06:00:00Z",
        bar_close_time_utc="2026-06-13T05:00:00Z",
        event_gross_cap=0.06,
        legs=[
            V17SignalLeg(
                event_id="v17-shadow:BTC:LONG:2026-06-13T05:00:00Z:2026-06-13T06:00:00Z",
                leg_id="v17-shadow:BTC:LONG:2026-06-13T05:00:00Z:2026-06-13T06:00:00Z:leg:0",
                symbol="BTC",
                side="LONG",
                quality_score=None,
                quality_label="UNAVAILABLE",
                raw_leg_weight=0.0,
                final_leg_weight=0.0,
                status="invalid",
                reason="missing_score",
            )
        ],
        status="invalid",
        reason="missing_score",
        strategy_version="V17_shadow",
    )

    class MissingScoreSignal:
        def to_event(self) -> V17SignalEvent:
            return event

    monkeypatch.setattr(shadow_runner, "build_candidate_signal", lambda *args, **kwargs: MissingScoreSignal())

    result = run_shadow_once(
        symbols=["BTC"],
        as_of="2026-06-13T06:00:00Z",
        config=config(),
        ohlcv_reader=FakeReader(),
        ledger=ledger,
        signal_writer=writer,
    )

    assert result.status == "invalid"
    assert result.invalid_count == 1
    assert result.events[0].reason == "missing_score"
    assert ledger.events[0].reason == "missing_score"
    assert writer.calls[0][0] == "v17.signals"


def test_runner_reports_duplicate_event_from_ledger() -> None:
    result = run_shadow_once(
        symbols=["BTC"],
        as_of="2026-06-13T06:00:00Z",
        config=config(),
        ohlcv_reader=FakeReader(),
        ledger=FakeLedger(duplicate=True),
        signal_writer=FakeSignalWriter(),
    )

    assert result.status == "duplicate"
    assert result.duplicate_count == 1
    assert result.events[0].duplicate is True
    assert result.events[0].reason == "duplicate_event"


def test_validate_config_rejects_production_or_live_settings() -> None:
    with pytest.raises(ValueError, match="production alpha.target"):
        validate_shadow_config(config(streams={**config()["streams"], "target": "alpha.target"}))
    with pytest.raises(ValueError, match="real_money_execution_allowed=false"):
        validate_shadow_config(config(real_money_execution_allowed=True))
    with pytest.raises(ValueError, match="mode=paper_shadow"):
        validate_shadow_config(config(mode="live"))
    with pytest.raises(ValueError, match="dry_run=true"):
        validate_shadow_config(config(dry_run=False))
    with pytest.raises(ValueError, match="V17-specific"):
        validate_shadow_config(config(streams={**config()["streams"], "signals": "alpha.signals"}))


def test_no_production_side_effect_imports(monkeypatch) -> None:
    blocked_imports: list[str] = []
    original_import = __import__

    def guard_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(
            (
                "redis",
                "services.execution",
                "services.risk_engine",
                "services.ai_decision",
                "services.market_data",
                "openai",
                "anthropic",
            )
        ):
            blocked_imports.append(name)
            raise AssertionError(f"unexpected live-path import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guard_import)

    before_modules = set(sys.modules)
    result = V17ShadowRunner(
        config=config(),
        ohlcv_reader=FakeReader(),
        ledger=FakeLedger(),
        signal_writer=FakeSignalWriter(),
    ).run(symbols=["BTC"], as_of="2026-06-13T06:00:00Z")
    after_modules = set(sys.modules)

    assert result.status == "valid"
    assert blocked_imports == []
    assert "services.execution" not in after_modules - before_modules
    assert "services.risk_engine" not in after_modules - before_modules
