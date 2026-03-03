import os
import sqlite3
import importlib
import sys
import json


def load_module(tmp_path):
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ["REPORT_DB_PATH"] = str(tmp_path / "reporting.db")
    mod_name = "services.reporting.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_reporting_db_schema(tmp_path):
    mod = load_module(tmp_path)
    conn = mod._ensure_db(str(tmp_path / "reporting.db"))
    cur = conn.execute("select name from sqlite_master where type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "exec_reports" in tables
    assert "state_snapshots" in tables
    assert "audit_events" in tables


def test_insert_exec_report(tmp_path):
    mod = load_module(tmp_path)
    conn = mod._ensure_db(str(tmp_path / "reporting.db"))
    env = {"ts": "2026-02-18T16:00:00Z", "cycle_id": "20260218T1600Z"}
    data = {
        "symbol": "BTC",
        "status": "ACK",
        "client_order_id": "c1",
        "exchange_order_id": "o1",
        "filled_qty": 0.1,
        "avg_px": 50000.0,
        "fee": 0.2,
        "latency_ms": 12,
        "raw": {"ok": True},
    }
    mod._insert_exec_report(conn, env, data)
    conn.commit()
    row = conn.execute("select symbol, status, filled_qty from exec_reports").fetchone()
    assert row == ("BTC", "ACK", 0.1)


def test_insert_state_snapshot(tmp_path):
    mod = load_module(tmp_path)
    conn = mod._ensure_db(str(tmp_path / "reporting.db"))
    env = {"ts": "2026-02-18T16:00:00Z", "cycle_id": "20260218T1600Z"}
    data = {"equity_usd": 1000.0, "cash_usd": 200.0, "positions": {"BTC": {"qty": 0.1}}}
    mod._insert_state_snapshot(conn, env, data)
    conn.commit()
    row = conn.execute("select equity_usd, cash_usd from state_snapshots").fetchone()
    assert row == (1000.0, 200.0)


def test_insert_audit_event(tmp_path):
    mod = load_module(tmp_path)
    conn = mod._ensure_db(str(tmp_path / "reporting.db"))
    env = {"ts": "2026-02-18T16:00:00Z", "cycle_id": "20260218T1600Z", "source": "ai_decision"}
    mod._insert_audit_event(conn, env, "ai.major_decision", {"used": "llm"})
    conn.commit()
    row = conn.execute("select event, data_json from audit_events").fetchone()
    assert row[0] == "ai.major_decision"
    assert json.loads(row[1])["used"] == "llm"
