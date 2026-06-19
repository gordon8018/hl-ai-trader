import importlib
import os
import re
import sys

from shared.schemas import Envelope, ExecutionReport, StateSnapshot


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    os.environ.setdefault("EXCHANGE", "hyperliquid")
    os.environ.setdefault("HL_PRIVATE_KEY", "0x" + "0" * 64)
    os.environ.setdefault("HL_ACCOUNT_ADDRESS", "0x" + "0" * 40)
    os.environ.setdefault("DRY_RUN", "true")
    mod_name = "services.portfolio_state.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_terminal_status_detection():
    mod = load_module()
    # _terminal_status now takes a string, not a dict
    assert mod._terminal_status("filled") is True
    assert mod._terminal_status("canceled") is True
    assert mod._terminal_status("rejected") is True
    assert mod._terminal_status("open") is False
    assert mod._terminal_status("ack") is False


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


def test_unhealthy_state_snapshot_preserves_schema_shape():
    mod = load_module()
    snapshot = mod._unhealthy_state_snapshot(last_reconcile_ts="2026-06-19T11:04:20Z")
    parsed = StateSnapshot(**snapshot.model_dump())

    assert parsed.equity_usd == 0.0
    assert parsed.cash_usd == 0.0
    assert parsed.positions == {}
    assert parsed.open_orders == []
    assert parsed.health["reconcile_ok"] is False
    assert parsed.health["last_reconcile_ts"] == "2026-06-19T11:04:20Z"
    assert parsed.health["mode"] == "reconcile_error"


def test_track_report_order_does_not_add_terminal_order_to_active_set():
    mod = load_module()

    class _Redis:
        def __init__(self):
            self.sets = {mod.ACTIVE_OIDS_SET: {"123"}}
            self.hashes = {
                mod.OID_CYCLE_MAP: {"123": "20260619T0105Z"},
                mod.OID_META_HASH: {"123": "{}"},
            }

        def sadd(self, key, value):
            self.sets.setdefault(key, set()).add(value)

        def srem(self, key, value):
            self.sets.setdefault(key, set()).discard(value)

        def hset(self, key, field, value):
            self.hashes.setdefault(key, {})[field] = value

        def hdel(self, key, field):
            self.hashes.setdefault(key, {}).pop(field, None)

        def expire(self, key, seconds):
            pass

    class _Bus:
        def __init__(self):
            self.r = _Redis()

    bus = _Bus()
    rep = ExecutionReport(
        client_order_id="cid",
        exchange_order_id="123",
        symbol="ETH",
        status="FILLED",
    )

    mod._track_report_order(bus, rep, "20260619T0105Z")

    assert "123" not in bus.r.sets[mod.ACTIVE_OIDS_SET]
    assert "123" not in bus.r.hashes[mod.OID_CYCLE_MAP]
    assert "123" not in bus.r.hashes[mod.OID_META_HASH]
