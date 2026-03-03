# services/reporting/app.py
import os
import json
import time
import sqlite3
from typing import Any, Dict

from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import Envelope
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

REDIS_URL = os.environ["REDIS_URL"]
SERVICE = "reporting"
os.environ["SERVICE_NAME"] = SERVICE

STREAM_REPORTS = "exec.reports"
STREAM_STATE = "state.snapshot"
STREAM_AUDIT = "audit.logs"

GROUP_REPORTS = "reporting_reports_grp"
GROUP_STATE = "reporting_state_grp"
GROUP_AUDIT = "reporting_audit_grp"

DB_PATH = os.environ.get("REPORT_DB_PATH", "data/reporting.db")
POLL_MS = int(os.environ.get("REPORT_POLL_MS", "1000"))
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))


def _ensure_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        create table if not exists exec_reports (
            id integer primary key autoincrement,
            ts text,
            cycle_id text,
            symbol text,
            status text,
            client_order_id text,
            exchange_order_id text,
            filled_qty real,
            avg_px real,
            fee real,
            latency_ms real,
            raw_json text
        )
        """
    )
    conn.execute("create index if not exists idx_exec_reports_ts on exec_reports(ts)")
    conn.execute("create index if not exists idx_exec_reports_symbol on exec_reports(symbol)")

    conn.execute(
        """
        create table if not exists state_snapshots (
            id integer primary key autoincrement,
            ts text,
            cycle_id text,
            equity_usd real,
            cash_usd real,
            positions_json text
        )
        """
    )
    conn.execute("create index if not exists idx_state_snapshots_ts on state_snapshots(ts)")

    conn.execute(
        """
        create table if not exists audit_events (
            id integer primary key autoincrement,
            ts text,
            cycle_id text,
            source text,
            event text,
            data_json text
        )
        """
    )
    conn.execute("create index if not exists idx_audit_events_ts on audit_events(ts)")
    conn.commit()
    return conn


def _insert_exec_report(conn: sqlite3.Connection, env: Dict[str, Any], data: Dict[str, Any]) -> None:
    conn.execute(
        """
        insert into exec_reports (
            ts, cycle_id, symbol, status, client_order_id, exchange_order_id,
            filled_qty, avg_px, fee, latency_ms, raw_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            env.get("ts"),
            env.get("cycle_id"),
            data.get("symbol"),
            data.get("status"),
            data.get("client_order_id"),
            data.get("exchange_order_id"),
            float(data.get("filled_qty", 0.0) or 0.0),
            float(data.get("avg_px", 0.0) or 0.0),
            float(data.get("fee", 0.0) or 0.0),
            float(data.get("latency_ms", 0.0) or 0.0),
            json.dumps(data.get("raw", {}), ensure_ascii=False),
        ),
    )


def _insert_state_snapshot(conn: sqlite3.Connection, env: Dict[str, Any], data: Dict[str, Any]) -> None:
    conn.execute(
        """
        insert into state_snapshots (ts, cycle_id, equity_usd, cash_usd, positions_json)
        values (?, ?, ?, ?, ?)
        """,
        (
            env.get("ts"),
            env.get("cycle_id"),
            float(data.get("equity_usd", 0.0) or 0.0),
            float(data.get("cash_usd", 0.0) or 0.0),
            json.dumps(data.get("positions", {}), ensure_ascii=False),
        ),
    )


def _insert_audit_event(conn: sqlite3.Connection, env: Dict[str, Any], event: str, data: Dict[str, Any]) -> None:
    conn.execute(
        """
        insert into audit_events (ts, cycle_id, source, event, data_json)
        values (?, ?, ?, ?, ?)
        """,
        (
            env.get("ts"),
            env.get("cycle_id"),
            env.get("source"),
            event,
            json.dumps(data, ensure_ascii=False),
        ),
    )


def main() -> None:
    start_metrics("METRICS_PORT", 9108)
    bus = RedisStreams(REDIS_URL)
    conn = _ensure_db(DB_PATH)

    error_streak = 0
    alarm_on = False

    def note_error(env: Envelope, where: str, err: Exception) -> None:
        nonlocal error_streak, alarm_on
        error_streak += 1
        ERR.labels(SERVICE, where).inc()
        if error_streak >= ERROR_STREAK_THRESHOLD and not alarm_on:
            alarm_on = True
            set_alarm("error_streak", True)
            bus.xadd_json("audit.logs", {"env": env.model_dump(), "event": "alarm", "where": where, "reason": "error_streak"})

    def note_ok(env: Envelope) -> None:
        nonlocal error_streak, alarm_on
        if error_streak > 0:
            error_streak = 0
        if alarm_on:
            alarm_on = False
            set_alarm("error_streak", False)
            bus.xadd_json("audit.logs", {"env": env.model_dump(), "event": "alarm_cleared", "reason": "recovered"})

    while True:
        t0 = time.time()
        try:
            # exec.reports
            for _, msg_id, payload in bus.xreadgroup_json(STREAM_REPORTS, GROUP_REPORTS, SERVICE, count=50, block_ms=POLL_MS):
                MSG_IN.labels(SERVICE, STREAM_REPORTS).inc()
                payload = require_env(payload)
                env = Envelope(**payload["env"])
                data = payload.get("data") if isinstance(payload, dict) else {}
                if isinstance(data, dict):
                    _insert_exec_report(conn, env.model_dump(), data)
                    conn.commit()
                bus.xack(STREAM_REPORTS, GROUP_REPORTS, msg_id)
                note_ok(env)

            # state.snapshot
            for _, msg_id, payload in bus.xreadgroup_json(STREAM_STATE, GROUP_STATE, SERVICE, count=20, block_ms=1):
                MSG_IN.labels(SERVICE, STREAM_STATE).inc()
                payload = require_env(payload)
                env = Envelope(**payload["env"])
                data = payload.get("data") if isinstance(payload, dict) else {}
                if isinstance(data, dict):
                    _insert_state_snapshot(conn, env.model_dump(), data)
                    conn.commit()
                bus.xack(STREAM_STATE, GROUP_STATE, msg_id)
                note_ok(env)

            # audit.logs
            for _, msg_id, payload in bus.xreadgroup_json(STREAM_AUDIT, GROUP_AUDIT, SERVICE, count=50, block_ms=1):
                MSG_IN.labels(SERVICE, STREAM_AUDIT).inc()
                payload = require_env(payload)
                env = Envelope(**payload["env"])
                event = payload.get("event", "") if isinstance(payload, dict) else ""
                data = payload.get("data", {}) if isinstance(payload, dict) else {}
                if isinstance(data, dict):
                    _insert_audit_event(conn, env.model_dump(), str(event), data)
                    conn.commit()
                bus.xack(STREAM_AUDIT, GROUP_AUDIT, msg_id)
                note_ok(env)

            LAT.labels(SERVICE, "loop").observe(time.time() - t0)
        except Exception as e:
            env = Envelope(source=SERVICE, cycle_id="UNKNOWN")
            note_error(env, "loop", e)
            time.sleep(1.0)


if __name__ == "__main__":
    main()
