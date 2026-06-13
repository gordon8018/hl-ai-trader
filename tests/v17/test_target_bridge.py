from __future__ import annotations

import sys
from typing import Any, Literal, Mapping

import pytest

from shared.schemas import TargetPortfolio
from shared.v17.schemas import V17SignalEvent, V17SignalLeg
from services.v17_shadow.target_bridge import (
    V17_TARGET_STREAM,
    V17TargetBridge,
    bridge_rows,
    event_from_ledger_rows,
    event_to_target_portfolio,
    validate_target_bridge_config,
)


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def write(self, stream: str, payload: Mapping[str, Any]) -> str:
        self.calls.append((stream, payload))
        return "ok"


def config(**overrides) -> dict[str, Any]:
    base = {
        "enabled": True,
        "mode": "paper_shadow",
        "dry_run": True,
        "real_money_execution_allowed": False,
        "streams": {
            "features_1h": "v17.features.1h",
            "signals": "v17.signals",
            "shadow_ledger": "v17.shadow.ledger",
            "target": V17_TARGET_STREAM,
        },
        "caps": {"max_event_gross": 0.06, "max_symbol_gross": 0.04, "max_gross": 0.10, "max_net": 0.08},
    }
    base.update(overrides)
    return base


def event(*, status: Literal["valid", "invalid", "blocked"] = "valid") -> V17SignalEvent:
    return V17SignalEvent(
        event_id="event-1",
        entry_time_utc="2026-06-13T06:00:00Z",
        bar_close_time_utc="2026-06-13T05:00:00Z",
        event_gross_cap=0.06,
        legs=[
            V17SignalLeg(
                event_id="event-1",
                leg_id="event-1:leg:0",
                symbol="BTC",
                side="LONG",
                quality_score=0.9,
                quality_label="Q1",
                raw_leg_weight=0.08,
                final_leg_weight=0.08,
                status="valid",
                reason="candidate",
            ),
            V17SignalLeg(
                event_id="event-1",
                leg_id="event-1:leg:1",
                symbol="ETH",
                side="SHORT",
                quality_score=0.8,
                quality_label="Q2",
                raw_leg_weight=0.05,
                final_leg_weight=-0.05,
                status="valid",
                reason="candidate",
            ),
        ],
        status=status,
        reason="shadow_event_aggregated",
        strategy_version="V17_shadow",
    )


def test_event_to_target_portfolio_uses_v17_model_metadata_and_caps() -> None:
    target = event_to_target_portfolio(event(), config=config())

    assert target.model == {"name": "v17", "version": "V17_shadow", "mode": "paper_shadow"}
    assert target.major_cycle_id == "event-1"
    assert target.decision_horizon == "1h"
    assert target.decision_action == "REBALANCE"
    assert target.universe == ["BTC", "ETH"]
    assert sum(abs(weight.weight) for weight in target.targets) <= 0.060000000001
    assert all(abs(weight.weight) <= 0.040000000001 for weight in target.targets)
    assert target.constraints_hint["event_gross_cap"] == 0.06
    assert target.constraints_hint["max_symbol_gross"] == 0.04
    assert target.evidence["source"] == "v17_shadow_target_bridge"
    assert target.evidence["mode"] == "paper_shadow"


def test_target_payload_roundtrips_through_existing_target_portfolio_schema() -> None:
    target = event_to_target_portfolio(event(), config=config())
    payload = target.model_dump(mode="json")

    reparsed = TargetPortfolio.model_validate(payload)

    assert reparsed == target
    assert payload["model"] == {"name": "v17", "version": "V17_shadow", "mode": "paper_shadow"}
    assert payload["major_cycle_id"] == "event-1"
    assert payload["decision_action"] == "REBALANCE"


def test_bridge_event_writes_only_isolated_v17_target_stream() -> None:
    writer = FakeWriter()
    result = V17TargetBridge(config=config(), writer=writer).bridge_event(event())

    assert result.status == "valid"
    assert result.reason == "target_written"
    assert result.stream == V17_TARGET_STREAM
    assert result.write_result == "ok"
    assert [call[0] for call in writer.calls] == [V17_TARGET_STREAM]
    assert writer.calls[0][0] != "alpha.target"
    assert writer.calls[0][1]["model"]["mode"] == "paper_shadow"


def test_bridge_blocks_non_valid_events_without_writing() -> None:
    writer = FakeWriter()
    blocked_event = event(status="invalid")

    result = V17TargetBridge(config=config(), writer=writer).bridge_event(blocked_event)

    assert result.status == "blocked"
    assert result.reason == "event_status:invalid"
    assert writer.calls == []


def test_bridge_rows_builds_event_from_valid_ledger_rows() -> None:
    rows = [
        {
            "event_id": "event-rows",
            "leg_id": "event-rows:leg:0",
            "symbol": "BTC",
            "side": "LONG",
            "quality_score": "0.9",
            "quality_label": "Q1",
            "raw_leg_weight": "0.03",
            "final_leg_weight": "0.03",
            "event_gross_cap": "0.06",
            "entry_time_utc": "2026-06-13T06:00:00Z",
            "bar_close_time_utc": "2026-06-13T05:00:00Z",
            "strategy_version": "V17_shadow",
            "status": "valid",
            "reason": "candidate",
        },
        {"event_id": "event-rows", "leg_id": "ignored", "status": "duplicate"},
    ]

    rebuilt = event_from_ledger_rows(rows, config=config())
    result = bridge_rows(rows, config=config())

    assert rebuilt is not None
    assert rebuilt.event_id == "event-rows"
    assert len(rebuilt.legs) == 1
    assert result.status == "valid"
    assert result.target is not None
    assert result.target.targets[0].symbol == "BTC"


def test_bridge_rows_blocks_when_ledger_has_no_valid_rows_without_writing() -> None:
    writer = FakeWriter()
    rows = [
        {"event_id": "event-blocked", "leg_id": "blocked", "status": "blocked"},
        {"event_id": "event-blocked", "leg_id": "duplicate", "status": "duplicate"},
    ]

    result = V17TargetBridge(config=config(), writer=writer).bridge_rows(rows)

    assert result.status == "blocked"
    assert result.reason == "no_valid_event_rows"
    assert result.stream == V17_TARGET_STREAM
    assert result.target is None
    assert writer.calls == []


def test_validate_target_bridge_config_rejects_production_or_live_settings() -> None:
    with pytest.raises(ValueError, match="production alpha.target"):
        validate_target_bridge_config(config(streams={**config()["streams"], "target": "alpha.target"}))
    with pytest.raises(ValueError, match="alpha.target.v17_shadow"):
        validate_target_bridge_config(config(streams={**config()["streams"], "target": "alpha.target.other"}))
    with pytest.raises(ValueError, match="real_money_execution_allowed=false"):
        validate_target_bridge_config(config(real_money_execution_allowed=True))
    with pytest.raises(ValueError, match="mode=paper_shadow"):
        validate_target_bridge_config(config(mode="live"))
    with pytest.raises(ValueError, match="dry_run=true"):
        validate_target_bridge_config(config(dry_run=False))


def test_no_live_path_imports_during_bridge(monkeypatch) -> None:
    blocked_imports: list[str] = []
    original_import = __import__

    def guard_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(("redis", "services.execution", "services.risk_engine", "services.ai_decision", "openai", "anthropic")):
            blocked_imports.append(name)
            raise AssertionError(f"unexpected live-path import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guard_import)

    before_modules = set(sys.modules)
    result = V17TargetBridge(config=config(), writer=FakeWriter()).bridge_event(event())
    after_modules = set(sys.modules)

    assert result.status == "valid"
    assert blocked_imports == []
    assert "services.execution" not in after_modules - before_modules
    assert "services.risk_engine" not in after_modules - before_modules
