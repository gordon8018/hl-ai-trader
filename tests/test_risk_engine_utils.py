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


def test_escalate_mode():
    mod = load_module()
    assert mod.escalate_mode("NORMAL", "REDUCE_ONLY") == "REDUCE_ONLY"
    assert mod.escalate_mode("REDUCE_ONLY", "HALT") == "HALT"
    assert mod.escalate_mode("NORMAL", "NORMAL") == "NORMAL"


def test_daily_loss_mode():
    mod = load_module()
    ratio = mod.calc_daily_loss_ratio(1000.0, 940.0)
    assert 0.059 <= ratio <= 0.061
    assert mod.mode_from_daily_loss(ratio, 0.03, 0.05) == ("HALT", "daily_loss_halt")
    assert mod.mode_from_daily_loss(0.035, 0.03, 0.05) == ("REDUCE_ONLY", "daily_loss_reduce_only")
    assert mod.mode_from_daily_loss(0.01, 0.03, 0.05) == ("NORMAL", "")


def test_reject_streak_mode():
    mod = load_module()
    streak = mod.consecutive_rejected_streak(["REJECTED", "REJECTED", "ACK", "REJECTED"])
    assert streak == 2
    assert mod.mode_from_reject_streak(2, 3, 5) == ("NORMAL", "")
    assert mod.mode_from_reject_streak(3, 3, 5) == ("REDUCE_ONLY", "consecutive_rejected_reduce_only")
    assert mod.mode_from_reject_streak(5, 3, 5) == ("HALT", "consecutive_rejected_halt")


def test_mode_from_market_quality_reduce_only():
    mod = load_module()
    features = {
        "reject_rate_15m": {"BTC": 0.20, "ETH": 0.10},
        "p95_latency_ms_15m": {"BTC": 1000.0, "ETH": 900.0},
        "liquidity_score": {"BTC": 0.5, "ETH": 0.4},
        "basis_bps": {"BTC": 10.0, "ETH": 8.0},
        "oi_change_15m": {"BTC": 0.05, "ETH": 0.04},
    }
    mode, reasons, stats = mod.mode_from_market_quality(features, ["BTC", "ETH"])
    assert mode == "REDUCE_ONLY"
    assert "execution_reject_rate_reduce_only" in reasons
    assert stats["reject_rate_avg"] >= 0.15


def test_mode_from_market_quality_halt():
    mod = load_module()
    features = {
        "reject_rate_15m": {"BTC": 0.01, "ETH": 0.02},
        "p95_latency_ms_15m": {"BTC": 9000.0, "ETH": 8500.0},
        "liquidity_score": {"BTC": 0.03, "ETH": 0.04},
        "basis_bps": {"BTC": 100.0, "ETH": 90.0},
        "oi_change_15m": {"BTC": 0.45, "ETH": 0.10},
    }
    mode, reasons, _ = mod.mode_from_market_quality(features, ["BTC", "ETH"])
    assert mode == "HALT"
    assert any("halt" in r for r in reasons)


def test_safe_xreadgroup_json_returns_empty_on_error():
    mod = load_module()

    class _Bus:
        def xreadgroup_json(self, *args, **kwargs):
            raise RuntimeError("redis timeout")

    msgs = mod.safe_xreadgroup_json(
        _Bus(),
        "alpha.target",
        "risk_grp",
        "risk_1",
        count=1,
        block_ms=1,
    )
    assert msgs == []


# ── M1: peak drawdown guard ──────────────────────────────────────────────────

class _FakeBusPeak:
    """Minimal bus stub for peak_equity_guard tests."""
    def __init__(self, stored_peak=None):
        self._store = {}
        if stored_peak is not None:
            self._store["risk.peak_equity"] = {"equity_usd": stored_peak, "ts": "2026-01-01T00:00:00Z"}

    def get_json(self, key):
        return self._store.get(key)

    def set_json(self, key, obj, ex=None):
        self._store[key] = obj


def test_peak_equity_guard_normal():
    mod = load_module()
    bus = _FakeBusPeak(stored_peak=1000.0)
    mode, reason, peak, dd = mod.peak_equity_guard(bus, 960.0)
    # 4% drawdown < 8% threshold → NORMAL
    assert mode == "NORMAL"
    assert abs(dd - 0.04) < 1e-9


def test_peak_equity_guard_halt():
    mod = load_module()
    bus = _FakeBusPeak(stored_peak=1000.0)
    mode, reason, peak, dd = mod.peak_equity_guard(bus, 900.0)
    # 10% drawdown >= 8% threshold → HALT
    assert mode == "HALT"
    assert reason == "peak_drawdown_halt"
    assert dd >= 0.08


def test_peak_equity_guard_updates_peak():
    mod = load_module()
    bus = _FakeBusPeak(stored_peak=800.0)
    mod.peak_equity_guard(bus, 1000.0)  # new equity > stored peak
    assert bus._store["risk.peak_equity"]["equity_usd"] == 1000.0


# ── M1: IC decay guard ───────────────────────────────────────────────────────

class _FakeBusIC:
    """Minimal bus stub for ic_decay_guard tests."""
    def __init__(self, ic_1h=None, ic_ts="2026-04-12T10:00:00Z", streak=0, last_ts=""):
        self._store = {}
        if ic_1h is not None:
            self._store["ic.signal_ic_latest"] = {"ic_1h": ic_1h, "ts": ic_ts}
        if streak:
            self._store["risk.ic_decay_streak"] = {"streak": streak, "last_ic_ts": last_ts, "ic_1h": ic_1h or 0.0}

    def get_json(self, key):
        return self._store.get(key)

    def set_json(self, key, obj, ex=None):
        self._store[key] = obj


def test_ic_decay_guard_no_ic_data():
    mod = load_module()
    mode, reason, streak = mod.ic_decay_guard(_FakeBusIC())
    assert mode == "NORMAL"
    assert streak == 0


def test_ic_decay_guard_positive_ic():
    mod = load_module()
    bus = _FakeBusIC(ic_1h=0.05, ic_ts="ts1")
    mode, _, streak = mod.ic_decay_guard(bus)
    assert mode == "NORMAL"
    assert streak == 0


def test_ic_decay_guard_below_threshold_increments_streak():
    mod = load_module()
    bus = _FakeBusIC(ic_1h=-0.05, ic_ts="ts_new", streak=2, last_ts="ts_old")
    mode, reason, streak = mod.ic_decay_guard(bus)
    # streak was 2, new observation → 3, which hits the reduce-only threshold
    assert mode == "REDUCE_ONLY"
    assert reason == "ic_decay_reduce_only"
    assert streak == 3


def test_ic_decay_guard_same_ts_no_double_count():
    mod = load_module()
    # same timestamp: streak should not increment again
    bus = _FakeBusIC(ic_1h=-0.05, ic_ts="same_ts", streak=1, last_ts="same_ts")
    mode, _, streak = mod.ic_decay_guard(bus)
    assert mode == "NORMAL"
    assert streak == 1  # unchanged


def test_ic_decay_guard_recovery_resets_streak():
    mod = load_module()
    bus = _FakeBusIC(ic_1h=0.02, ic_ts="ts_recovery", streak=2, last_ts="ts_old")
    mode, _, streak = mod.ic_decay_guard(bus)
    assert mode == "NORMAL"
    assert streak == 0
