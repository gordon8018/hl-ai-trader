import json

import pytest
from pydantic import ValidationError

from shared.schemas import TargetPortfolio
from shared.v17.schemas import (
    V17FeatureSnapshot,
    V17LedgerRow,
    V17SignalEvent,
    V17SignalLeg,
)


def test_feature_snapshot_serializes_for_redis() -> None:
    snapshot = V17FeatureSnapshot(
        symbol="BTC",
        bar_close_time_utc="2026-06-13T16:00:00Z",
        entry_time_utc="2026-06-13T16:01:00Z",
        side="LONG",
        features={"atr_pct": 0.021, "volume_rank": 0.84},
        status="valid",
        reason="quality_inputs_ready",
    )

    payload = snapshot.to_dict()
    encoded = json.dumps(payload)

    assert json.loads(encoded) == payload
    assert V17FeatureSnapshot.from_dict(payload).to_dict() == payload


def test_signal_event_roundtrip_and_target_portfolio_bridge() -> None:
    leg = V17SignalLeg(
        event_id="evt-20260613T1600Z",
        leg_id="evt-20260613T1600Z:BTC",
        symbol="BTC",
        side="LONG",
        quality_score=0.91,
        quality_label="Q1",
        raw_leg_weight=0.18,
        final_leg_weight=0.12,
        status="valid",
        reason="capped_to_event_gross",
    )
    event = V17SignalEvent(
        event_id="evt-20260613T1600Z",
        entry_time_utc="2026-06-13T17:00:00Z",
        bar_close_time_utc="2026-06-13T16:00:00Z",
        event_gross_cap=0.12,
        legs=[leg],
        reason="single_leg_event",
        strategy_version="v17.0",
    )

    payload = event.to_dict()
    rebuilt = V17SignalEvent.from_dict(json.loads(json.dumps(payload)))
    target = rebuilt.to_target_portfolio()

    assert rebuilt.to_dict() == payload
    assert isinstance(target, TargetPortfolio)
    assert target.asof_minute == event.entry_time_utc
    assert target.universe == ["BTC"]
    assert target.targets[0].symbol == "BTC"
    assert target.targets[0].weight == 0.12
    assert target.model == {"name": "v17", "version": "v17.0"}
    assert target.constraints_hint == {"event_gross_cap": 0.12}
    assert target.major_cycle_id == "evt-20260613T1600Z"


def test_invalid_and_blocked_ledger_rows_are_structurally_valid() -> None:
    invalid = V17LedgerRow(
        event_id="evt-invalid",
        leg_id="evt-invalid:SOL",
        entry_time_utc="2026-06-13T17:00:00Z",
        bar_close_time_utc="2026-06-13T16:00:00Z",
        symbol="SOL",
        side="FLAT",
        quality_score=None,
        quality_label="UNAVAILABLE",
        event_gross_cap=0.0,
        raw_leg_weight=0.0,
        final_leg_weight=0.0,
        status="invalid",
        reason="insufficient_history",
    )
    blocked = V17LedgerRow(
        event_id="evt-blocked",
        leg_id="evt-blocked:ETH",
        entry_time_utc="2026-06-13T17:00:00Z",
        bar_close_time_utc="2026-06-13T16:00:00Z",
        symbol="ETH",
        side="SHORT",
        quality_score=0.72,
        quality_label="Q2",
        event_gross_cap=0.10,
        raw_leg_weight=-0.08,
        final_leg_weight=0.0,
        status="blocked",
        reason="shadow_config_disabled",
    )

    assert invalid.to_dict()["status"] == "invalid"
    assert blocked.to_dict()["status"] == "blocked"


def test_required_fields_and_structural_validation() -> None:
    with pytest.raises(ValidationError):
        V17LedgerRow(
            event_id="evt-missing-symbol",
            leg_id="evt-missing-symbol:BTC",
            entry_time_utc="2026-06-13T17:00:00Z",
            bar_close_time_utc="2026-06-13T16:00:00Z",
            side="LONG",
            quality_score=0.5,
            quality_label="Q3",
            event_gross_cap=0.10,
            raw_leg_weight=0.05,
            final_leg_weight=0.05,
            status="valid",
        )

    with pytest.raises(ValidationError):
        V17SignalLeg(
            event_id="evt-bad-weight",
            leg_id="evt-bad-weight:BTC",
            symbol="BTC",
            side="LONG",
            quality_score=0.5,
            quality_label="Q3",
            raw_leg_weight=0.05,
            final_leg_weight=-0.05,
            status="valid",
        )

    with pytest.raises(ValidationError):
        V17SignalEvent(
            event_id="evt-parent",
            entry_time_utc="2026-06-13T17:00:00Z",
            bar_close_time_utc="2026-06-13T16:00:00Z",
            event_gross_cap=0.10,
            legs=[
                V17SignalLeg(
                    event_id="evt-child",
                    leg_id="evt-child:BTC",
                    symbol="BTC",
                    side="LONG",
                    quality_score=0.5,
                    quality_label="Q3",
                    raw_leg_weight=0.05,
                    final_leg_weight=0.05,
                    status="valid",
                )
            ],
        )
