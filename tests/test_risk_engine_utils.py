import importlib
import os
import sys


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
