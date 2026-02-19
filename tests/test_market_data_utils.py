import importlib
import os
import sys
from datetime import datetime, timezone


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    mod_name = "services.market_data.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_utc_minute_key():
    mod = load_module()
    dt = datetime(2026, 2, 18, 16, 0, 59, tzinfo=timezone.utc)
    ts = dt.timestamp()
    assert mod.utc_minute_key(ts) == "2026-02-18T16:00:00Z"


def test_calc_return_positive():
    mod = load_module()
    assert abs(mod.calc_return(100.0, 110.0) - 0.1) < 1e-9


def test_calc_return_non_positive():
    mod = load_module()
    assert mod.calc_return(0.0, 110.0) == 0.0
    assert mod.calc_return(-1.0, 110.0) == 0.0
    assert mod.calc_return(100.0, -1.0) == 0.0
