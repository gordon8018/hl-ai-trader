from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

from shared.schemas import Position, StateSnapshot


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    os.environ.setdefault("DRY_RUN", "true")
    os.environ.pop("V17_LIVE_EXECUTION", None)
    os.environ.pop("V17_LIVE_REAL_MONEY_APPROVED", None)
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_state(health: dict[str, object]) -> StateSnapshot:
    return StateSnapshot(
        equity_usd=1000.0,
        cash_usd=1000.0,
        positions={
            "BTC": Position(
                symbol="BTC",
                qty=0.0,
                entry_px=0.0,
                mark_px=50000.0,
                unreal_pnl=0.0,
                side="FLAT",
            ),
            "ETH": Position(
                symbol="ETH",
                qty=0.0,
                entry_px=0.0,
                mark_px=3000.0,
                unreal_pnl=0.0,
                side="FLAT",
            ),
        },
        open_orders=[],
        health=health,
    )


def test_validate_live_state_snapshot_rejects_missing_snapshot() -> None:
    mod = load_module()

    assert mod.validate_live_state_snapshot(None, max_age_seconds=60) == (
        False,
        "missing_state_snapshot",
    )


def test_validate_live_state_snapshot_rejects_unhealthy_reconcile() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": False,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc)),
        }
    )

    assert mod.validate_live_state_snapshot(st, max_age_seconds=60) == (
        False,
        "reconcile_unhealthy",
    )


def test_validate_live_state_snapshot_prioritizes_reconcile_unhealthy_in_live() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": False,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc)),
            "mode": "reconcile_error",
        }
    )

    assert mod.validate_live_state_snapshot(
        st, max_age_seconds=60, require_live_source=True
    ) == (
        False,
        "reconcile_unhealthy",
    )


_health_reconcile_not_true_cases = [
    {},
    {"reconcile_ok": None},
    {"reconcile_ok": "false"},
]


def test_validate_live_state_snapshot_rejects_reconcile_ok_not_true() -> None:
    mod = load_module()

    for health in _health_reconcile_not_true_cases:
        st = make_state(
            {
                **health,
                "last_reconcile_ts": iso_z(datetime.now(timezone.utc)),
            }
        )

        assert mod.validate_live_state_snapshot(st, max_age_seconds=60) == (
            False,
            "reconcile_unhealthy",
        )


def test_validate_live_state_snapshot_rejects_future_reconcile_timestamp() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": True,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc) + timedelta(seconds=10)),
        }
    )

    assert mod.validate_live_state_snapshot(st, max_age_seconds=60) == (
        False,
        "future_reconcile_timestamp",
    )


def test_validate_live_state_snapshot_rejects_missing_reconcile_timestamp() -> None:
    mod = load_module()
    st = make_state({"reconcile_ok": True})

    assert mod.validate_live_state_snapshot(st, max_age_seconds=60) == (
        False,
        "missing_reconcile_timestamp",
    )


def test_validate_live_state_snapshot_rejects_stale_snapshot() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": True,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc) - timedelta(seconds=120)),
        }
    )

    assert mod.validate_live_state_snapshot(st, max_age_seconds=60) == (
        False,
        "stale_state_snapshot",
    )


def test_validate_live_state_snapshot_accepts_fresh_snapshot() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": True,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc) - timedelta(seconds=5)),
        }
    )

    assert mod.validate_live_state_snapshot(st, max_age_seconds=60) == (True, "ok")



def test_validate_live_state_snapshot_rejects_paper_shadow_for_live_trading() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": True,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc)),
            "mode": "paper_shadow",
        }
    )

    assert mod.validate_live_state_snapshot(
        st,
        max_age_seconds=60,
        require_live_source=True,
    ) == (False, "paper_shadow_state_in_live")


def test_validate_live_state_snapshot_accepts_exchange_reconciled_live_state() -> None:
    mod = load_module()
    st = make_state(
        {
            "reconcile_ok": True,
            "last_reconcile_ts": iso_z(datetime.now(timezone.utc)),
            "mode": "live_exchange",
        }
    )

    assert mod.validate_live_state_snapshot(
        st,
        max_age_seconds=60,
        require_live_source=True,
    ) == (True, "ok")
