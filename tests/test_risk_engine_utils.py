import importlib
import os
import sys
from datetime import datetime, timezone, timedelta

from shared.schemas import StateSnapshot, Position


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.risk_engine.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_cap_for_btc_eth():
    mod = load_module()
    os.environ["CAP_BTC_ETH"] = "0.12"
    os.environ["CAP_ALT"] = "0.05"
    assert abs(mod.cap_for("BTC") - 0.12) < 1e-9
    assert abs(mod.cap_for("ETH") - 0.12) < 1e-9
    assert abs(mod.cap_for("SOL") - 0.05) < 1e-9


def test_health_mode_reconcile_unhealthy_halt():
    mod = load_module()
    st = StateSnapshot(
        equity_usd=1000.0,
        cash_usd=1000.0,
        positions={"BTC": Position(symbol="BTC", qty=0.0, entry_px=0.0, mark_px=50000.0, unreal_pnl=0.0, side="FLAT")},
        health={"reconcile_ok": False},
    )
    mode, rejs = mod.health_mode({}, st, stale_secs=45)
    assert mode == "HALT"
    assert any(r.reason == "reconcile_unhealthy" for r in rejs)


def test_health_mode_stale_reduce_only():
    mod = load_module()
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    st = StateSnapshot(
        equity_usd=1000.0,
        cash_usd=1000.0,
        positions={"BTC": Position(symbol="BTC", qty=0.0, entry_px=0.0, mark_px=50000.0, unreal_pnl=0.0, side="FLAT")},
        health={"reconcile_ok": True, "last_reconcile_ts": old_ts},
    )
    mode, rejs = mod.health_mode({}, st, stale_secs=45)
    assert mode == "REDUCE_ONLY"
    assert any(r.reason == "state_stale_reduce_only" for r in rejs)


def test_merge_modes():
    mod = load_module()
    assert mod.merge_modes("NORMAL", "HALT") == "HALT"
    assert mod.merge_modes("NORMAL", "REDUCE_ONLY") == "REDUCE_ONLY"
    assert mod.merge_modes("REDUCE_ONLY", "NORMAL") == "REDUCE_ONLY"
