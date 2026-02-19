import importlib
import os
import sys


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_extract_order_status_top_level():
    mod = load_module()
    assert mod.extract_order_status({"status": "filled"}) == "filled"


def test_extract_order_status_nested():
    mod = load_module()
    assert mod.extract_order_status({"order": {"status": "open"}}) == "open"


def test_classify_order_status():
    mod = load_module()
    assert mod.classify_order_status("filled") == "FILLED"
    assert mod.classify_order_status("canceled") == "CANCELED"
    assert mod.classify_order_status("tickRejected") == "REJECTED"
    assert mod.classify_order_status("open") == "OPEN"
    assert mod.classify_order_status("unknown") == "UNKNOWN"
    assert mod.classify_order_status(None) is None
