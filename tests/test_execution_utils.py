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
