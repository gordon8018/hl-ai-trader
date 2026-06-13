from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from services.v17_data_builder.ohlcv_cache import OHLCVCacheReader
from services.v17_shadow.ledger import V17ShadowLedger
from services.v17_shadow.runner import run_shadow_once, validate_shadow_config
from services.v17_shadow.target_bridge import V17_TARGET_STREAM, V17TargetBridge

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "v17" / "multi_symbol_1h.csv"
AS_OF = "2026-06-13T06:00:00Z"


class RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def write(self, stream: str, payload: Mapping[str, Any]) -> str:
        self.calls.append((stream, payload))
        return "ok"


class ExchangeSubmitTrap:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    def submit(self, payload: Mapping[str, Any]) -> None:
        self.calls.append(payload)
        raise AssertionError("real exchange submit path must not be called")


def replay_config(ledger_path: Path) -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "paper_shadow",
        "dry_run": True,
        "real_money_execution_allowed": False,
        "ohlcv_cache_path": str(FIXTURE_PATH),
        "streams": {
            "features_1h": "v17.features.1h",
            "signals": "v17.signals",
            "shadow_ledger": "v17.shadow.ledger",
            "target": V17_TARGET_STREAM,
        },
        "ledger": {"path": str(ledger_path)},
        "caps": {
            "max_event_gross": 0.06,
            "max_symbol_gross": 0.04,
            "max_gross": 0.10,
            "max_net": 0.08,
        },
        "freshness": {"max_completed_bar_lag_seconds": 7200},
    }


def test_full_multi_symbol_replay_is_deterministic_capped_duplicate_safe_and_isolated(tmp_path: Path) -> None:
    ledger_path = tmp_path / "v17_shadow_ledger.csv"
    config = replay_config(ledger_path)
    reader = OHLCVCacheReader.from_csv(FIXTURE_PATH, freshness_tolerance_seconds=7200)
    ledger = V17ShadowLedger(ledger_path)
    signal_writer = RecordingWriter()
    target_writer = RecordingWriter()
    exchange = ExchangeSubmitTrap()

    validate_shadow_config(config)

    first = run_shadow_once(
        symbols=["BTC", "ETH"],
        as_of=AS_OF,
        sides={"BTC": "LONG", "ETH": "SHORT"},
        config=config,
        ohlcv_reader=reader,
        ledger=ledger,
        signal_writer=signal_writer,
    )

    assert first.status == "valid"
    assert first.processed_count == 1
    assert first.valid_count == 1
    event = first.events[0].event
    assert event is not None
    assert event.event_id == "v17-shadow-event:V17_shadow:2026-06-13T05:00:00Z:2026-06-13T06:00:00Z:BTC+ETH"
    assert len(event.legs) == 2
    assert {leg.symbol for leg in event.legs} == {"BTC", "ETH"}
    assert sum(abs(leg.final_leg_weight) for leg in event.legs) <= 0.060000000001
    assert event.reason == "shadow_event_aggregated:cap_scaled"
    assert all(leg.leg_id.startswith(f"{event.event_id}:leg:") for leg in event.legs)

    bridge_result = V17TargetBridge(config=config, writer=target_writer).bridge_event(event)
    assert bridge_result.status == "valid"
    assert bridge_result.stream == V17_TARGET_STREAM
    assert target_writer.calls == [(V17_TARGET_STREAM, target_writer.calls[0][1])]
    assert target_writer.calls[0][0] != "alpha.target"
    assert bridge_result.target is not None
    assert bridge_result.target.model == {"name": "v17", "version": "V17_shadow", "mode": "paper_shadow"}
    assert sum(abs(target.weight) for target in bridge_result.target.targets) <= 0.060000000001

    second = run_shadow_once(
        symbols=["BTC", "ETH"],
        as_of=AS_OF,
        sides={"BTC": "LONG", "ETH": "SHORT"},
        config=config,
        ohlcv_reader=reader,
        ledger=ledger,
        signal_writer=signal_writer,
    )

    assert second.status == "duplicate"
    assert second.duplicate_count == 1
    duplicate_event = second.events[0].event
    assert duplicate_event is not None
    assert duplicate_event.event_id == event.event_id
    assert [leg.leg_id for leg in duplicate_event.legs] == [leg.leg_id for leg in event.legs]

    rows = ledger.read_rows()
    valid_rows = [row for row in rows if row["status"] == "valid"]
    duplicate_rows = [row for row in rows if row["status"] == "duplicate"]
    assert len(valid_rows) == 2
    assert len(duplicate_rows) == 2
    assert sum(abs(row["final_leg_weight"]) for row in valid_rows) <= 0.060000000001
    assert sum(abs(row["final_leg_weight"]) for row in duplicate_rows) == 0.0
    assert [call[0] for call in signal_writer.calls] == ["v17.signals", "v17.signals"]
    assert "alpha.target" not in [call[0] for call in signal_writer.calls]
    assert exchange.calls == []


def test_full_replay_rejects_production_target_stream(tmp_path: Path) -> None:
    config = replay_config(tmp_path / "ledger.csv")
    config["streams"] = {**config["streams"], "target": "alpha.target"}

    with pytest.raises(ValueError, match="production alpha.target"):
        validate_shadow_config(config)
    with pytest.raises(ValueError, match="production alpha.target"):
        V17TargetBridge(config=config, writer=RecordingWriter())
