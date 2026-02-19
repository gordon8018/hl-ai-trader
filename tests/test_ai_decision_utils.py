import importlib
import os
import sys


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_normalize_nonnegative():
    mod = load_module()
    weights = {"BTC": 2.0, "ETH": -1.0}
    norm = mod.normalize(weights)
    assert abs(sum(norm.values()) - 1.0) < 1e-9
    assert norm["ETH"] == 0.0


def test_normalize_zero():
    mod = load_module()
    norm = mod.normalize({"BTC": 0.0, "ETH": 0.0})
    assert norm["BTC"] == 0.0
    assert norm["ETH"] == 0.0
