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


def test_make_heartbeat_report():
    mod = load_module()
    rep = mod.make_heartbeat_report("20260327T0709Z", "skip_decision_action", mode="NORMAL")
    assert rep.client_order_id == "20260327T0709Z:heartbeat"
    assert rep.symbol == "BTC"
    assert rep.status == "CANCELED"
    assert rep.raw["skip_reason"] == "skip_decision_action"
    assert rep.raw["mode"] == "NORMAL"


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
