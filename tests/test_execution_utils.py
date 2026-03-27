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
