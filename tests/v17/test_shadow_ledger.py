from __future__ import annotations

import sys

import pytest

from services.v17_shadow.ledger import V17ShadowLedger, append_event, append_row, read_rows
from shared.v17.schemas import V17LedgerRow, V17SignalEvent, V17SignalLeg


def ledger_row(
    *,
    event_id: str = "event-1",
    leg_id: str = "event-1:leg:0",
    status: str = "valid",
    raw_leg_weight: float = 0.03,
    final_leg_weight: float = 0.03,
    reason: str = "accepted",
) -> V17LedgerRow:
    return V17LedgerRow(
        event_id=event_id,
        leg_id=leg_id,
        entry_time_utc="2026-06-13T06:00:00Z",
        bar_close_time_utc="2026-06-13T05:00:00Z",
        symbol="BTC",
        side="LONG",
        quality_score=0.72,
        quality_label="Q2",
        event_gross_cap=0.06,
        raw_leg_weight=raw_leg_weight,
        final_leg_weight=final_leg_weight,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        strategy_version="V17_shadow",
    )


def signal_event(*, status: str = "valid") -> V17SignalEvent:
    leg_status = "blocked" if status == "blocked" else status
    return V17SignalEvent(
        event_id="event-2",
        entry_time_utc="2026-06-13T07:00:00Z",
        bar_close_time_utc="2026-06-13T06:00:00Z",
        event_gross_cap=0.05,
        status=status,  # type: ignore[arg-type]
        reason="event_reason",
        strategy_version="V17_shadow",
        legs=[
            V17SignalLeg(
                event_id="event-2",
                leg_id="event-2:leg:0",
                symbol="ETH",
                side="SHORT",
                quality_score=0.8,
                quality_label="Q1",
                raw_leg_weight=0.04,
                final_leg_weight=-0.04,
                status=leg_status,  # type: ignore[arg-type]
                reason="leg_reason",
            )
        ],
    )


def test_append_row_creates_append_only_csv_and_readback(tmp_path) -> None:
    path = tmp_path / "shadow" / "ledger.csv"
    ledger = V17ShadowLedger(path)

    result = ledger.append_row(ledger_row())
    rows = ledger.read_rows()

    assert result.status == "valid"
    assert result.duplicate is False
    assert path.exists()
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-1"
    assert rows[0]["leg_id"] == "event-1:leg:0"
    assert rows[0]["quality_score"] == pytest.approx(0.72)
    assert rows[0]["raw_leg_weight"] == pytest.approx(0.03)
    assert rows[0]["final_leg_weight"] == pytest.approx(0.03)
    assert rows[0]["status"] == "valid"


def test_duplicate_guard_appends_audit_row_with_zero_weights(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    ledger = V17ShadowLedger(path)

    first = ledger.append_row(ledger_row(reason="first_accept"))
    duplicate = ledger.append_row(ledger_row(reason="first_accept"))
    rows = ledger.read_rows()

    assert first.status == "valid"
    assert duplicate.status == "duplicate"
    assert duplicate.duplicate is True
    assert len(rows) == 2
    assert rows[0]["status"] == "valid"
    assert rows[1]["status"] == "duplicate"
    assert rows[1]["raw_leg_weight"] == pytest.approx(0.0)
    assert rows[1]["final_leg_weight"] == pytest.approx(0.0)
    assert rows[1]["reason"] == "duplicate_event_leg:first_accept"


def test_invalid_and_blocked_rows_are_preserved(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    ledger = V17ShadowLedger(path)

    ledger.append_row(ledger_row(event_id="invalid-event", leg_id="invalid-leg", status="invalid", raw_leg_weight=0.0, final_leg_weight=0.0, reason="bad_input"))
    ledger.append_row(ledger_row(event_id="blocked-event", leg_id="blocked-leg", status="blocked", raw_leg_weight=0.02, final_leg_weight=0.0, reason="risk_block"))
    rows = ledger.read_rows()

    assert [row["status"] for row in rows] == ["invalid", "blocked"]
    assert rows[0]["reason"] == "bad_input"
    assert rows[1]["reason"] == "risk_block"
    assert rows[1]["final_leg_weight"] == pytest.approx(0.0)


def test_append_event_uses_leg_fields_and_event_status(tmp_path) -> None:
    path = tmp_path / "ledger.csv"

    results = append_event(path, signal_event(status="valid"))
    rows = read_rows(path)

    assert len(results) == 1
    assert results[0].status == "valid"
    assert rows[0]["event_id"] == "event-2"
    assert rows[0]["symbol"] == "ETH"
    assert rows[0]["side"] == "SHORT"
    assert rows[0]["final_leg_weight"] == pytest.approx(-0.04)
    assert rows[0]["reason"] == "leg_reason"


def test_blocked_event_overrides_leg_status(tmp_path) -> None:
    path = tmp_path / "ledger.csv"

    results = append_event(path, signal_event(status="blocked"))
    rows = read_rows(path)

    assert results[0].status == "blocked"
    assert rows[0]["status"] == "blocked"


def test_duplicate_guard_applies_to_event_replay_without_double_counting(tmp_path) -> None:
    path = tmp_path / "ledger.csv"

    first_results = append_event(path, signal_event(status="valid"))
    replay_results = append_event(path, signal_event(status="valid"))
    rows = read_rows(path)

    assert [result.status for result in first_results] == ["valid"]
    assert [result.status for result in replay_results] == ["duplicate"]
    assert len(rows) == 2
    assert rows[0]["status"] == "valid"
    assert rows[0]["final_leg_weight"] == pytest.approx(-0.04)
    assert rows[1]["status"] == "duplicate"
    assert rows[1]["raw_leg_weight"] == pytest.approx(0.0)
    assert rows[1]["final_leg_weight"] == pytest.approx(0.0)


def test_duplicate_guard_counts_invalid_and_blocked_keys_as_seen(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    ledger = V17ShadowLedger(path)

    ledger.append_row(ledger_row(status="invalid", raw_leg_weight=0.0, final_leg_weight=0.0, reason="bad_input"))
    duplicate_invalid = ledger.append_row(ledger_row(status="invalid", raw_leg_weight=0.0, final_leg_weight=0.0, reason="bad_input"))
    ledger.append_row(ledger_row(event_id="blocked-event", leg_id="blocked-leg", status="blocked", raw_leg_weight=0.02, final_leg_weight=0.0, reason="risk_block"))
    duplicate_blocked = ledger.append_row(ledger_row(event_id="blocked-event", leg_id="blocked-leg", status="blocked", raw_leg_weight=0.02, final_leg_weight=0.0, reason="risk_block"))
    rows = ledger.read_rows()

    assert duplicate_invalid.status == "duplicate"
    assert duplicate_blocked.status == "duplicate"
    assert [row["status"] for row in rows] == ["invalid", "duplicate", "blocked", "duplicate"]
    assert rows[1]["final_leg_weight"] == pytest.approx(0.0)
    assert rows[3]["raw_leg_weight"] == pytest.approx(0.0)


def test_rejects_duplicate_status_and_missing_keys(tmp_path) -> None:
    path = tmp_path / "ledger.csv"

    with pytest.raises(ValueError, match="unsupported ledger status"):
        append_row(path, {**ledger_row().to_dict(), "status": "duplicate"})
    with pytest.raises(ValueError, match="event_id and leg_id"):
        append_row(path, {**ledger_row().to_dict(), "event_id": ""})


def test_no_production_side_effect_imports(monkeypatch, tmp_path) -> None:
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
    result = append_row(tmp_path / "ledger.csv", ledger_row())
    after_modules = set(sys.modules)

    assert result.status == "valid"
    assert blocked_imports == []
    assert "services.execution" not in after_modules - before_modules
    assert "services.risk_engine" not in after_modules - before_modules
