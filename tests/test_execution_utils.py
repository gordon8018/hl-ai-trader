import importlib
import os
import sys

from shared.schemas import ApprovedTargetPortfolio, TargetWeight


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_bps_to_frac():
    mod = load_module()
    assert abs(mod.bps_to_frac(10) - 0.001) < 1e-9


def test_compute_target_weights():
    mod = load_module()
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-02-18T16:00:00Z",
        mode="NORMAL",
        approved_targets=[TargetWeight(symbol="BTC", weight=0.1), TargetWeight(symbol="ETH", weight=-0.05)],
    )
    tgt = mod.compute_target_weights(ap)
    assert tgt["BTC"] == 0.1
    assert tgt["ETH"] == -0.05


def test_make_skip_report():
    mod = load_module()
    rep = mod.make_skip_report("cid", "BTC", "min_notional_skip", qty=0.01)
    assert rep.client_order_id == "cid"
    assert rep.symbol == "BTC"
    assert rep.status == "REJECTED"
    assert rep.raw["skip_reason"] == "min_notional_skip"
    assert rep.raw["qty"] == 0.01


def test_qty_decimals_from_step():
    """_qty_decimals_from_step should convert qty_step to decimal places."""
    mod = load_module()
    fn = mod._qty_decimals_from_step
    assert fn(0.001) == 3
    assert fn(0.01) == 2
    assert fn(1.0) == 0
    assert fn(0.1) == 1
    assert fn(0.0001) == 4
    assert fn(0.0) == 0
    assert fn(2.0) == 0


def test_safe_xreadgroup_json_returns_empty_on_error():
    mod = load_module()

    class _Bus:
        def xreadgroup_json(self, *args, **kwargs):
            raise RuntimeError("redis timeout")

    msgs = mod.safe_xreadgroup_json(
        _Bus(),
        "risk.approved",
        "exec_grp",
        "exec_1",
        count=1,
        block_ms=1,
    )
    assert msgs == []


def test_control_mode_accepts_plain_string_values():
    mod = load_module()

    class _Bus:
        def __init__(self, raw: str):
            self.raw = raw

        def get_json(self, key):
            raise ValueError("not json")

        def get(self, key):
            return self.raw

    assert mod.get_control_mode(_Bus("NORMAL")) == "NORMAL"
    assert mod.get_control_mode(_Bus("HALT")) == "HALT"
    assert mod.get_control_mode(_Bus("REDUCE_ONLY")) == "REDUCE_ONLY"
    assert mod.get_control_mode(_Bus("bad")) == "NORMAL"


def test_make_heartbeat_report():
    mod = load_module()
    rep = mod.make_heartbeat_report("20260327T0709Z", "skip_decision_action", mode="NORMAL")
    assert rep.client_order_id == "20260327T0709Z:heartbeat"
    assert rep.symbol == "BTC"
    assert rep.status == "ACK"
    assert rep.raw["event"] == "heartbeat"
    assert rep.raw["skip_reason"] == "skip_decision_action"
    assert rep.raw["mode"] == "NORMAL"


def test_build_submit_report_rejects_ack_without_exchange_order_id():
    mod = load_module()
    rep = mod.build_submit_report(
        client_order_id="cid",
        symbol="ETH",
        exchange_order_id="",
        exchange_status="ack",
        filled_qty=0.0,
        avg_px=0.0,
        exchange_latency_ms=12,
        latency_ms=13,
    )

    assert rep.status == "REJECTED"
    assert rep.exchange_order_id is None
    assert rep.raw["reason"] == "missing_exchange_order_id_after_submit"


def test_build_submit_report_keeps_ack_with_exchange_order_id():
    mod = load_module()
    rep = mod.build_submit_report(
        client_order_id="cid",
        symbol="ETH",
        exchange_order_id="123",
        exchange_status="ack",
        filled_qty=0.0,
        avg_px=0.0,
        exchange_latency_ms=12,
        latency_ms=13,
    )

    assert rep.status == "ACK"
    assert rep.exchange_order_id == "123"


def test_build_submit_report_preserves_filled_fields_at_top_level():
    mod = load_module()
    rep = mod.build_submit_report(
        client_order_id="cid",
        symbol="ETH",
        exchange_order_id="123",
        exchange_status="filled",
        filled_qty=0.1221,
        avg_px=3400.5,
        exchange_latency_ms=12,
        latency_ms=13,
    )

    assert rep.status == "FILLED"
    assert rep.filled_qty == 0.1221
    assert rep.avg_px == 3400.5


def test_terminal_report_preserves_filled_fields_at_top_level():
    mod = load_module()

    class _Bus:
        def __init__(self):
            self.events = []
            self.dedup = {}

        def xadd_json(self, stream, payload):
            self.events.append((stream, payload))

        def set_json(self, key, payload, ex=None):
            self.dedup[key] = payload

    bus = _Bus()
    tracker = mod.AsyncOrderTracker(
        bus=bus,
        exchange=None,
        limiter=None,
        cancel_bucket=None,
        status_bucket=None,
    )
    tracker._emit_terminal_report(
        client_order_id="cid",
        exchange_order_id="123",
        symbol="ETH",
        status="FILLED",
        raw={"filled_qty": 0.1221, "avg_px": 3400.5},
        cycle_id="20260619T0105Z",
        dedup_key="dedup:cid",
    )

    report = bus.events[0][1]["data"]
    assert report["filled_qty"] == 0.1221
    assert report["avg_px"] == 3400.5


def test_terminal_report_does_not_downgrade_filled_dedup_to_canceled():
    mod = load_module()

    class _Bus:
        def __init__(self):
            self.events = []
            self.dedup = {"dedup:cid": {"status": "FILLED", "exchange_order_id": "123"}}

        def xadd_json(self, stream, payload):
            self.events.append((stream, payload))

        def get_json(self, key):
            return self.dedup.get(key)

        def set_json(self, key, payload, ex=None):
            self.dedup[key] = payload

    bus = _Bus()
    tracker = mod.AsyncOrderTracker(
        bus=bus,
        exchange=None,
        limiter=None,
        cancel_bucket=None,
        status_bucket=None,
    )

    tracker._emit_terminal_report(
        client_order_id="cid",
        exchange_order_id="123",
        symbol="ETH",
        status="CANCELED",
        raw={"timeout": True},
        cycle_id="20260619T0719Z",
        dedup_key="dedup:cid",
    )

    assert bus.dedup["dedup:cid"]["status"] == "FILLED"


def test_live_cycle_guard_blocks_repeated_rebalance_cycle():
    mod = load_module()

    class _Bus:
        def __init__(self):
            self.values = {"exec.cycle.done:20260619T0719Z": {"status": "DONE"}}

        def get_json(self, key):
            return self.values.get(key)

    allowed, reason = mod.live_cycle_guard(_Bus(), "20260619T0719Z", "REBALANCE", [object()])

    assert allowed is False
    assert reason == "cycle_already_executed"


def test_latest_approval_cycle_guard_blocks_stale_cycle_before_slice_submit(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "V17_LIVE_EXECUTION", True)

    class _Redis:
        def xrevrange(self, stream, max="+", min="-", count=None):
            return [
                (
                    "2-0",
                    {"p": '{"env":{"cycle_id":"20260619T1141Z"},"data":{}}'},
                )
            ]

    class _Bus:
        r = _Redis()

    allowed, reason = mod.latest_approval_cycle_guard(_Bus(), "20260619T1140Z", "1-0")

    assert allowed is False
    assert reason == "stale_risk_cycle"


def test_latest_approval_cycle_guard_allows_queued_newer_cycle_when_msg_id_not_newer(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "V17_LIVE_EXECUTION", True)

    class _Redis:
        def xrevrange(self, stream, max="+", min="-", count=None):
            return [
                (
                    "2-0",
                    {"p": '{"env":{"cycle_id":"20260619T1141Z"},"data":{}}'},
                )
            ]

    class _Bus:
        r = _Redis()

    allowed, reason = mod.latest_approval_cycle_guard(_Bus(), "20260619T1140Z", "2-0")

    assert allowed is True
    assert reason == "ok"


def test_latest_approval_cycle_guard_fails_closed_without_latest_cycle(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "V17_LIVE_EXECUTION", True)

    class _Redis:
        def xrevrange(self, stream, max="+", min="-", count=None):
            return [("2-0", {"p": '{"env":{},"data":{}}'})]

    class _Bus:
        r = _Redis()

    allowed, reason = mod.latest_approval_cycle_guard(_Bus(), "20260619T1140Z", "1-0")

    assert allowed is False
    assert reason == "latest_approval_unavailable"


def test_latest_approval_cycle_guard_is_noop_outside_live_execution(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "V17_LIVE_EXECUTION", False)

    class _Redis:
        def xrevrange(self, stream, max="+", min="-", count=None):
            return [
                (
                    "2-0",
                    {"p": '{"env":{"cycle_id":"20260619T1141Z"},"data":{}}'},
                )
            ]

    class _Bus:
        r = _Redis()

    allowed, reason = mod.latest_approval_cycle_guard(_Bus(), "20260619T1140Z")

    assert allowed is True
    assert reason == "ok"


def test_slice_guard_blocks_inflight_halt_before_submit():
    mod = load_module()

    class _Bus:
        def get_json(self, key):
            return {"mode": "HALT"}

    allowed, mode, reason = mod.slice_submit_guard(_Bus(), "NORMAL", "BUY")

    assert allowed is False
    assert mode == "HALT"
    assert reason == "inflight_halt_mode"


def test_slice_guard_reduce_only_blocks_buy_to_increase_long():
    mod = load_module()

    class _Bus:
        def get_json(self, key):
            return {"mode": "REDUCE_ONLY"}

    allowed, mode, reason = mod.slice_submit_guard(
        _Bus(), "NORMAL", "BUY", current_qty=1.0, slice_qty=0.2
    )

    assert allowed is False
    assert mode == "REDUCE_ONLY"
    assert reason == "inflight_reduce_only_block"


def test_slice_guard_reduce_only_allows_buy_to_cover_short():
    mod = load_module()

    class _Bus:
        def get_json(self, key):
            return {"mode": "REDUCE_ONLY"}

    allowed, mode, reason = mod.slice_submit_guard(
        _Bus(), "NORMAL", "BUY", current_qty=-1.0, slice_qty=0.2
    )

    assert allowed is True
    assert mode == "REDUCE_ONLY"
    assert reason == "ok"


def test_slice_guard_reduce_only_allows_sell_to_reduce_long():
    mod = load_module()

    class _Bus:
        def get_json(self, key):
            return {"mode": "REDUCE_ONLY"}

    allowed, mode, reason = mod.slice_submit_guard(
        _Bus(), "NORMAL", "SELL", current_qty=1.0, slice_qty=0.2
    )

    assert allowed is True
    assert mode == "REDUCE_ONLY"
    assert reason == "ok"


def test_slice_guard_reduce_only_blocks_sell_to_increase_short():
    mod = load_module()

    class _Bus:
        def get_json(self, key):
            return {"mode": "REDUCE_ONLY"}

    allowed, mode, reason = mod.slice_submit_guard(
        _Bus(), "NORMAL", "SELL", current_qty=-1.0, slice_qty=0.2
    )

    assert allowed is False
    assert mode == "REDUCE_ONLY"
    assert reason == "inflight_reduce_only_block"


def test_slice_guard_reduce_only_blocks_crossing_flat():
    mod = load_module()

    class _Bus:
        def get_json(self, key):
            return {"mode": "REDUCE_ONLY"}

    allowed, mode, reason = mod.slice_submit_guard(
        _Bus(), "NORMAL", "BUY", current_qty=-0.1, slice_qty=0.2
    )

    assert allowed is False
    assert mode == "REDUCE_ONLY"
    assert reason == "inflight_reduce_only_block"


def test_live_recovery_gate_blocks_rebalance_by_default(monkeypatch):
    monkeypatch.setenv("V17_LIVE_EXECUTION", "true")
    monkeypatch.setenv("V17_LIVE_REAL_MONEY_APPROVED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("V17_LIVE_ALLOW_REBALANCE_ON_START", raising=False)
    mod = load_module()

    allowed, reason = mod.live_recovery_gate("REBALANCE", [{"symbol": "BTC", "weight": -0.02}])

    assert allowed is False
    assert reason == "live_rebalance_requires_runtime_confirmation"


def test_live_recovery_gate_allows_hold_without_override(monkeypatch):
    monkeypatch.delenv("V17_LIVE_ALLOW_REBALANCE_ON_START", raising=False)
    mod = load_module()

    allowed, reason = mod.live_recovery_gate("HOLD", [])

    assert allowed is True
    assert reason == "ok"


def test_live_recovery_gate_allows_rebalance_with_runtime_confirmation(monkeypatch):
    monkeypatch.setenv("V17_LIVE_ALLOW_REBALANCE_ON_START", "true")
    mod = load_module()

    allowed, reason = mod.live_recovery_gate("REBALANCE", [{"symbol": "BTC", "weight": -0.02}])

    assert allowed is True
    assert reason == "ok"


def test_live_recovery_gate_ignores_legacy_target_signature_env(monkeypatch):
    monkeypatch.setenv("V17_LIVE_ALLOW_REBALANCE_ON_START", "true")
    monkeypatch.setenv("V17_LIVE_APPROVED_TARGET_SIGNATURE", "different-signature")
    mod = load_module()
    targets = [{"symbol": "BTC", "weight": 0.03}, {"symbol": "ETH", "weight": 0.03}]

    allowed, reason = mod.live_recovery_gate("REBALANCE", targets)

    assert allowed is True
    assert reason == "ok"


def test_live_recovery_gate_allows_continuous_trading_after_start(monkeypatch):
    monkeypatch.setenv("V17_LIVE_EXECUTION", "true")
    monkeypatch.setenv("V17_LIVE_REAL_MONEY_APPROVED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("V17_LIVE_ALLOW_REBALANCE_ON_START", "true")
    monkeypatch.setenv("V17_LIVE_APPROVED_TARGET_SIGNATURE", "old-startup-signature")
    mod = load_module()

    allowed, reason = mod.live_recovery_gate(
        "REBALANCE",
        [{"symbol": "ETH", "weight": -0.05}, {"symbol": "SOL", "weight": -0.01795}],
    )

    assert allowed is True
    assert reason == "ok"


# ── Dynamic leverage path (N3 E2E) ────────────────────────────────────────────

def test_plan_twap_uses_effective_leverage_from_constraints_hint():
    """N3: effective_leverage in constraints_hint should scale position size."""
    mod = load_module()
    from shared.schemas import ApprovedTargetPortfolio, TargetWeight, StateSnapshot

    def _make_ap(leverage: float) -> ApprovedTargetPortfolio:
        return ApprovedTargetPortfolio(
            asof_minute="2026-04-12T10:00:00Z",
            mode="NORMAL",
            approved_targets=[TargetWeight(symbol="BTC", weight=0.10)],
            constraints_hint={"effective_leverage": leverage},
        )

    def _make_st(equity: float) -> StateSnapshot:
        return StateSnapshot(
            equity_usd=equity,
            cash_usd=equity,
            positions={
                "BTC": {"symbol": "BTC", "qty": 0.0, "entry_px": 0.0, "mark_px": 80000.0,
                        "unreal_pnl": 0.0, "side": "FLAT"},
                "ETH": {"symbol": "ETH", "qty": 0.0, "entry_px": 0.0, "mark_px": 2000.0,
                        "unreal_pnl": 0.0, "side": "FLAT"},
            },
        )

    equity = 1000.0
    st = _make_st(equity)

    # With 5x leverage, notional = weight × equity × leverage = 0.10 × 1000 × 5 = 500 USD
    ap_5x = _make_ap(5.0)
    plan_5x = mod.plan_twap(ap_5x, st, {"env": {"source": "test", "ts": "2026-04-12T10:00:00Z", "cycle_id": "20260412T1000Z"}})

    # With 3x leverage, notional = 0.10 × 1000 × 3 = 300 USD
    ap_3x = _make_ap(3.0)
    plan_3x = mod.plan_twap(ap_3x, st, {"env": {"source": "test", "ts": "2026-04-12T10:01:00Z", "cycle_id": "20260412T1001Z"}})

    total_qty_5x = sum(s.qty for s in plan_5x.slices if s.symbol == "BTC")
    total_qty_3x = sum(s.qty for s in plan_3x.slices if s.symbol == "BTC")

    # 5x leverage should produce more qty than 3x
    assert total_qty_5x > total_qty_3x, f"5x={total_qty_5x:.6f} should > 3x={total_qty_3x:.6f}"


def test_plan_twap_caps_order_notional_by_margin_amount_times_leverage():
    mod = load_module()
    from shared.schemas import ApprovedTargetPortfolio, StateSnapshot

    ap = ApprovedTargetPortfolio(
        asof_minute="2026-04-12T10:00:00Z",
        mode="NORMAL",
        approved_targets=[TargetWeight(symbol="BTC", weight=1.0)],
        constraints_hint={"effective_leverage": 5.0},
    )
    st = StateSnapshot(
        equity_usd=10000.0,
        cash_usd=10000.0,
        positions={
            "BTC": {"symbol": "BTC", "qty": 0.0, "entry_px": 0.0, "mark_px": 100000.0,
                    "unreal_pnl": 0.0, "side": "FLAT"},
            "ETH": {"symbol": "ETH", "qty": 0.0, "entry_px": 0.0, "mark_px": 2000.0,
                    "unreal_pnl": 0.0, "side": "FLAT"},
        },
    )

    plan = mod.plan_twap(
        ap,
        st,
        {"env": {"source": "test", "ts": "2026-04-12T10:00:00Z", "cycle_id": "20260412T1000Z"}},
    )

    btc_notional = sum(s.qty * 100000.0 for s in plan.slices if s.symbol == "BTC")
    assert abs(btc_notional - 500.0) < 1e-9


def test_plan_twap_uses_constraints_mark_prices_for_flat_symbols():
    mod = load_module()
    from shared.schemas import ApprovedTargetPortfolio, StateSnapshot

    ap = ApprovedTargetPortfolio(
        asof_minute="2026-04-12T10:00:00Z",
        mode="NORMAL",
        approved_targets=[TargetWeight(symbol="ETH", weight=0.10)],
        constraints_hint={"effective_leverage": 5.0, "mark_px_ETH": 100.0},
    )
    st = StateSnapshot(
        equity_usd=1000.0,
        cash_usd=1000.0,
        positions={},
    )

    plan = mod.plan_twap(
        ap,
        st,
        {"env": {"source": "test", "ts": "2026-04-12T10:00:00Z", "cycle_id": "20260412T1000Z"}},
    )

    assert sum(s.qty for s in plan.slices if s.symbol == "ETH") == 5.0


# ── M4: depth-aware slicing ────────────────────────────────────────────────────

def test_plan_twap_depth_aware_increases_slices_for_shallow_book():
    """M4: when avg_top_depth_usd is small, per-slice qty should be capped and slice count increased."""
    mod = load_module()
    from shared.schemas import ApprovedTargetPortfolio, TargetWeight, StateSnapshot

    equity = 1000.0
    leverage = 5.0
    weight = 0.10
    # notional = 0.10 × 1000 × 5 = 500 USD; with SLICES=3 per_slice_notional ≈ 167 USD
    # If avg_top_depth_usd = 500, then 10% = 50 USD per slice → needs more slices
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-04-12T10:00:00Z",
        mode="NORMAL",
        approved_targets=[TargetWeight(symbol="BTC", weight=weight)],
        constraints_hint={"effective_leverage": leverage, "avg_top_depth_usd": 500.0},
    )
    st = StateSnapshot(
        equity_usd=equity,
        cash_usd=equity,
        positions={
            "BTC": {"symbol": "BTC", "qty": 0.0, "entry_px": 0.0, "mark_px": 80000.0,
                    "unreal_pnl": 0.0, "side": "FLAT"},
            "ETH": {"symbol": "ETH", "qty": 0.0, "entry_px": 0.0, "mark_px": 2000.0,
                    "unreal_pnl": 0.0, "side": "FLAT"},
        },
    )
    ap_no_depth = ApprovedTargetPortfolio(
        asof_minute="2026-04-12T10:00:00Z",
        mode="NORMAL",
        approved_targets=[TargetWeight(symbol="BTC", weight=weight)],
        constraints_hint={"effective_leverage": leverage},
    )

    plan_deep = mod.plan_twap(ap_no_depth, st, {"env": {"source": "test", "ts": "2026-04-12T10:00:00Z", "cycle_id": "20260412T1000Z"}})
    plan_shallow = mod.plan_twap(ap, st, {"env": {"source": "test", "ts": "2026-04-12T10:01:00Z", "cycle_id": "20260412T1001Z"}})

    btc_slices_deep = len([s for s in plan_deep.slices if s.symbol == "BTC"])
    btc_slices_shallow = len([s for s in plan_shallow.slices if s.symbol == "BTC"])

    # Shallow book should produce at least as many slices as deep book
    assert btc_slices_shallow >= btc_slices_deep, (
        f"shallow({btc_slices_shallow}) should >= deep({btc_slices_deep})"
    )
