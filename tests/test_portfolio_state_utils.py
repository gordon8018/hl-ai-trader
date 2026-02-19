import importlib
import os
import re
import sys

from shared.schemas import Envelope


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.portfolio_state.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_terminal_status_detection():
    mod = load_module()
    assert mod._terminal_status({"status": "filled"}) is True
    assert mod._terminal_status({"status": "canceled"}) is True
    assert mod._terminal_status({"status": "rejected"}) is True
    assert mod._terminal_status({"status": "open"}) is False


def test_event_env_from_report_uses_report_cycle_id():
    mod = load_module()
    report_env = Envelope(source="execution", cycle_id="20260218T1600Z").model_dump()
    env = mod._event_env_from_report(report_env)
    assert env.cycle_id == "20260218T1600Z"
    assert env.source == mod.SERVICE


def test_event_env_for_reconcile_format():
    mod = load_module()
    env = mod._event_env_for_reconcile()
    assert re.match(r"^\d{8}T\d{4}Z$", env.cycle_id)
    assert env.source == mod.SERVICE
