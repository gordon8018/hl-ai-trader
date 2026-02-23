import importlib
import os
import sys

from shared.schemas import (
    ApprovedTargetPortfolio,
    TargetWeight,
    StateSnapshot,
    Position,
    Envelope,
)


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    os.environ.setdefault("TWAP_SLICES", "2")
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def make_state():
    return StateSnapshot(
        equity_usd=1000.0,
        cash_usd=200.0,
        positions={
            "BTC": Position(symbol="BTC", qty=0.01, entry_px=45000.0, mark_px=50000.0, unreal_pnl=50.0, side="LONG"),
            "ETH": Position(symbol="ETH", qty=0.0, entry_px=0.0, mark_px=3000.0, unreal_pnl=0.0, side="FLAT"),
        },
        open_orders=[],
        health={},
    )


def test_compute_current_weights():
    mod = load_module()
    st = make_state()
    w = mod.compute_current_weights(st)
    assert w["BTC"] == (0.01 * 50000.0) / 1000.0
    assert w["ETH"] == 0.0


def test_plan_twap_generates_slices():
    mod = load_module()
    st = make_state()
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-02-18T16:00:00Z",
        mode="NORMAL",
        approved_targets=[TargetWeight(symbol="BTC", weight=0.0), TargetWeight(symbol="ETH", weight=0.1)],
    )
    payload = {"env": Envelope(source="risk_engine", cycle_id="20260218T1600Z").model_dump()}
    plan = mod.plan_twap(ap, st, payload)
    assert plan.cycle_id == "20260218T1600Z"
    # BTC target reduces existing exposure; ETH target increases from 0.
    # With TWAP_SLICES=2, expect 2 slices per symbol.
    assert len(plan.slices) == 4


def test_reduce_only_skips_increasing_exposure():
    mod = load_module()
    st = make_state()
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-02-18T16:00:00Z",
        mode="REDUCE_ONLY",
        # current BTC weight is 0.5; target 0.6 would increase exposure and should be skipped
        approved_targets=[TargetWeight(symbol="BTC", weight=0.6)],
    )
    payload = {"env": Envelope(source="risk_engine", cycle_id="20260218T1600Z").model_dump()}
    plan = mod.plan_twap(ap, st, payload)
    assert len(plan.slices) == 0


def test_reduce_only_allows_reducing():
    mod = load_module()
    st = make_state()
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-02-18T16:00:00Z",
        mode="REDUCE_ONLY",
        approved_targets=[TargetWeight(symbol="BTC", weight=0.0)],
    )
    payload = {"env": Envelope(source="risk_engine", cycle_id="20260218T1600Z").model_dump()}
    plan = mod.plan_twap(ap, st, payload)
    assert len(plan.slices) == 2  # TWAP_SLICES=2 for BTC reduction


def test_dynamic_slices_coalesce_for_min_notional():
    mod = load_module()
    st = make_state()
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-02-18T16:00:00Z",
        mode="NORMAL",
        # ETH mark=3000, target weight 0.015 => total notional=15 USD.
        # With MIN_NOTIONAL=10 and safety ratio 1.02, only 1 slice is valid.
        approved_targets=[TargetWeight(symbol="BTC", weight=0.5), TargetWeight(symbol="ETH", weight=0.015)],
    )
    payload = {"env": Envelope(source="risk_engine", cycle_id="20260218T1600Z").model_dump()}
    plan = mod.plan_twap(ap, st, payload)
    assert len(plan.slices) == 1
    assert plan.slices[0].symbol == "ETH"


def test_dynamic_slices_skip_when_total_notional_below_min():
    mod = load_module()
    st = make_state()
    ap = ApprovedTargetPortfolio(
        asof_minute="2026-02-18T16:00:00Z",
        mode="NORMAL",
        # ETH target notional=8 USD, below minimum, should be skipped at planning time.
        approved_targets=[TargetWeight(symbol="BTC", weight=0.5), TargetWeight(symbol="ETH", weight=0.008)],
    )
    payload = {"env": Envelope(source="risk_engine", cycle_id="20260218T1600Z").model_dump()}
    plan = mod.plan_twap(ap, st, payload)
    assert len(plan.slices) == 0
