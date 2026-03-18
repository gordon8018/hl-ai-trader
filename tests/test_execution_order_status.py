import importlib
import os
import sys


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)



def test_timeout_terminal_decision():
    mod = load_module()
    assert mod.timeout_terminal_decision(cancel_allowed=False) == ("REJECTED", "timeout_cancel_rate_limited")
    assert mod.timeout_terminal_decision(cancel_allowed=True, cancel_error="boom") == ("REJECTED", "timeout_cancel_error")
    assert mod.timeout_terminal_decision(cancel_allowed=True, cancel_error=None) == ("CANCELED", "timeout_cancel")
