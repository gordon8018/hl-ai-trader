# services/portfolio_state/app.py
import os
import time
import httpx
from datetime import datetime, timezone
from typing import Dict, Set, Optional, Tuple

from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import Envelope, StateSnapshot, Position, OpenOrder, ExecutionReport
from shared.time_utils import current_cycle_id  # ✅ uses UTC minute
from pydantic import ValidationError
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

SERVICE = "portfolio_state"
os.environ["SERVICE_NAME"] = SERVICE

REDIS_URL = os.environ["REDIS_URL"]
HL_HTTP_URL = os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz")
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")

ACCOUNT = os.environ.get("HL_ACCOUNT_ADDRESS", "").strip()
POLL_SECONDS = float(os.environ.get("STATE_POLL_SECONDS", "10.0"))
STATUS_POLL_SECONDS = float(os.environ.get("ORDER_STATUS_POLL_SECONDS", "5.0"))
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

STREAM_SNAPSHOT = "state.snapshot"
STREAM_EVENTS = "state.events"
STREAM_REPORTS = "exec.reports"
AUDIT = "audit.logs"

LATEST_STATE_KEY = "latest.state.snapshot"

# active orders and mapping to cycle_id
ACTIVE_OIDS_SET = "active.exchange.oids"          # redis set of oids(str)
OID_CYCLE_MAP = "active.exchange.oid_cycle_map"   # redis hash: oid(str) -> cycle_id(str)

GROUP_REPORTS = "state_reports_grp"
CONSUMER = os.environ.get("CONSUMER", "state_1")

def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def order_status(client: httpx.Client, base_url: str, user: str, oid: int) -> Dict:
    r = client.post(f"{base_url}/info", json={"type": "orderStatus", "user": user, "oid": oid})
    r.raise_for_status()
    return r.json()

def _terminal_status(payload: Dict) -> bool:
    s = str(payload).lower()
    return ("filled" in s) or ("canceled" in s) or ("rejected" in s)

def _event_env_for_reconcile() -> Envelope:
    # ✅ 定时对账：使用 current_cycle_id()
    return Envelope(source=SERVICE, cycle_id=current_cycle_id())

def _event_env_from_report(report_env: Dict) -> Envelope:
    # ✅ 来自订单回报：使用 report 的 cycle_id
    # 注意：report_env 必须在上游已被严格校验；这里再构造一次确保格式正确
    incoming = Envelope(**report_env)
    return Envelope(source=SERVICE, cycle_id=incoming.cycle_id)

def main():
    start_metrics("METRICS_PORT", 9102)

    bus = RedisStreams(REDIS_URL)
    error_streak = 0
    alarm_on = False

    def note_error(env: Envelope, where: str, err: Exception) -> None:
        nonlocal error_streak, alarm_on
        error_streak += 1
        ERR.labels(SERVICE, where).inc()
        if error_streak >= ERROR_STREAK_THRESHOLD and not alarm_on:
            alarm_on = True
            set_alarm("error_streak", True)
            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "alarm", "where": where, "reason": "error_streak"}))

    def note_ok(env: Envelope) -> None:
        nonlocal error_streak, alarm_on
        if error_streak > 0:
            error_streak = 0
        if alarm_on:
            alarm_on = False
            set_alarm("error_streak", False)
            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "alarm_cleared", "reason": "recovered"}))

    if not ACCOUNT:
        # 用 current_cycle_id() 保证 audit 也符合 schema
        env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "warn", "msg": "HL_ACCOUNT_ADDRESS not set; state will be empty"}))

    with httpx.Client(timeout=8.0) as client:
        last_snapshot_ts = 0.0
        last_status_ts = 0.0

        while True:
            now = time.time()

            # ------------------------------------------------------------
            # 1) Consume exec.reports -> update active oids + emit state.events
            #    ✅ state.events from reports MUST use report.cycle_id
            # ------------------------------------------------------------
            try:
                msgs = bus.xreadgroup_json(STREAM_REPORTS, GROUP_REPORTS, CONSUMER, count=200, block_ms=10)
                for stream, msg_id, payload in msgs:
                    MSG_IN.labels(SERVICE, stream).inc()
                    try:
                        try:
                            payload = require_env(payload)
                            rep_env = _event_env_from_report(payload.get("env", {}))
                        except (KeyError, ValidationError, ValueError) as e:
                            env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                            dlq = {
                                "env": env.model_dump(),
                                "event": "protocol_error",
                                "reason": str(e),
                                "data": payload,
                            }
                            bus.xadd_json(f"dlq.{stream}", dlq)
                            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "protocol_error", "reason": str(e), "data": payload}))
                            bus.xack(STREAM_REPORTS, GROUP_REPORTS, msg_id)
                            ERR.labels(SERVICE, "protocol").inc()
                            note_error(env, "protocol", e)
                            continue

                        # parse report
                        try:
                            rep = ExecutionReport(**payload["data"])
                        except ValidationError as e:
                            bus.xadd_json(
                                f"dlq.{stream}",
                                {
                                    "env": rep_env.model_dump(),
                                    "event": "schema_error",
                                    "schema": "ExecutionReport",
                                    "reason": str(e),
                                    "data": payload.get("data"),
                                },
                            )
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": rep_env.model_dump(),
                                        "event": "schema_error",
                                        "schema": "ExecutionReport",
                                        "reason": str(e),
                                        "data": payload.get("data"),
                                    }
                                ),
                            )
                            bus.xack(STREAM_REPORTS, GROUP_REPORTS, msg_id)
                            ERR.labels(SERVICE, "schema").inc()
                            note_error(rep_env, "schema", e)
                            continue

                        # cycle_id from report env
                        rep_env = _event_env_from_report(payload.get("env", {}))

                        # If there is an exchange order id, track it and map oid -> cycle_id
                        if rep.exchange_order_id:
                            try:
                                oid_int = int(rep.exchange_order_id)
                                oid_s = str(oid_int)
                                bus.r.sadd(ACTIVE_OIDS_SET, oid_s)
                                bus.r.hset(OID_CYCLE_MAP, oid_s, rep_env.cycle_id)
                                # keep for a day
                                bus.r.expire(ACTIVE_OIDS_SET, 24 * 3600)
                                bus.r.expire(OID_CYCLE_MAP, 24 * 3600)
                            except Exception:
                                pass

                        # Emit a state.event for the report itself (optional but useful)
                        bus.xadd_json(
                            STREAM_EVENTS,
                            require_env({"env": rep_env.model_dump(), "event": "exec.report", "data": payload["data"]}),
                        )
                        MSG_OUT.labels(SERVICE, STREAM_EVENTS).inc()

                        bus.xack(STREAM_REPORTS, GROUP_REPORTS, msg_id)
                        note_ok(rep_env)
                    except Exception as e:
                        # business errors: keep pending
                        env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "where": "consume_reports", "err": str(e), "raw": payload}))
                        note_error(env, "consume_reports", e)
            except Exception as e:
                env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "where": "consume_reports_outer", "err": str(e)}))
                note_error(env, "consume_reports_outer", e)

            # ------------------------------------------------------------
            # 2) Periodic reconcile snapshot
            #    ✅ state.snapshot MUST use current_cycle_id()
            # ------------------------------------------------------------
            if ACCOUNT and (now - last_snapshot_ts) >= POLL_SECONDS:
                last_snapshot_ts = now
                env = _event_env_for_reconcile()
                try:
                    ch = client.post(f"{HL_HTTP_URL}/info", json={"type": "clearinghouseState", "user": ACCOUNT})
                    ch.raise_for_status()
                    chj = ch.json()

                    oo = client.post(f"{HL_HTTP_URL}/info", json={"type": "openOrders", "user": ACCOUNT})
                    oo.raise_for_status()
                    ooj = oo.json() or []

                    mids = client.post(f"{HL_HTTP_URL}/info", json={"type": "allMids"})
                    mids.raise_for_status()
                    midj: Dict[str, str] = mids.json()

                    equity = float(chj.get("marginSummary", {}).get("accountValue", 0.0) or 0.0)
                    cash = float(chj.get("withdrawable", 0.0) or 0.0)

                    positions: Dict[str, Position] = {}
                    for ap in chj.get("assetPositions", []):
                        pos = ap.get("position", ap)
                        coin = pos.get("coin") or pos.get("symbol")
                        if not coin or coin not in UNIVERSE:
                            continue
                        sz = float(pos.get("szi", pos.get("sz", 0.0)) or 0.0)
                        entry = float(pos.get("entryPx", pos.get("entry", 0.0)) or 0.0)
                        mark = float(midj.get(coin, entry or 0.0) or 0.0)
                        upl = float(pos.get("unrealizedPnl", pos.get("upl", 0.0)) or 0.0)
                        side = "FLAT"
                        if sz > 0:
                            side = "LONG"
                        elif sz < 0:
                            side = "SHORT"
                        positions[coin] = Position(symbol=coin, qty=sz, entry_px=entry, mark_px=mark, unreal_pnl=upl, side=side)

                    for sym in UNIVERSE:
                        if sym not in positions:
                            mark = float(midj.get(sym, 0.0) or 0.0)
                            positions[sym] = Position(symbol=sym, qty=0.0, entry_px=0.0, mark_px=mark, unreal_pnl=0.0, side="FLAT")

                    open_orders = []
                    for o in ooj:
                        coin = o.get("coin") or o.get("symbol")
                        if coin not in UNIVERSE:
                            continue
                        is_buy = bool(o.get("side") == "B" or o.get("isBuy") is True)
                        side = "BUY" if is_buy else "SELL"
                        px = float(o.get("limitPx", o.get("px", 0.0)) or 0.0)
                        sz = float(o.get("sz", o.get("size", 0.0)) or 0.0)
                        oid = str(o.get("oid", o.get("orderId", "")) or "")
                        cloid = str(o.get("cloid", o.get("clientOrderId", "")) or "")
                        open_orders.append(
                            OpenOrder(
                                client_order_id=cloid or oid or "unknown",
                                exchange_order_id=oid or None,
                                symbol=coin,
                                side=side,
                                px=px,
                                qty=sz,
                                status="OPEN",
                            )
                        )

                    st = StateSnapshot(
                        equity_usd=equity,
                        cash_usd=cash,
                        positions=positions,
                        open_orders=open_orders,
                        health={"last_reconcile_ts": iso_now(), "reconcile_ok": True},
                    )

                    # ✅ snapshot uses reconcile env (current_cycle_id)
                    bus.xadd_json(STREAM_SNAPSHOT, require_env({"env": env.model_dump(), "data": st.model_dump()}))
                    bus.set_json(LATEST_STATE_KEY, {"env": env.model_dump(), "data": st.model_dump()}, ex=60)
                    MSG_OUT.labels(SERVICE, STREAM_SNAPSHOT).inc()

                    # optional reconcile event (same env)
                    bus.xadd_json(
                        STREAM_EVENTS,
                        require_env({"env": env.model_dump(), "event": "reconcile", "data": {"equity_usd": equity, "cash_usd": cash}}),
                    )
                    MSG_OUT.labels(SERVICE, STREAM_EVENTS).inc()
                    note_ok(env)
                except Exception as e:
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "where": "reconcile", "err": str(e)}))
                    # mark reconcile unhealthy
                    bus.set_json(LATEST_STATE_KEY, {"env": env.model_dump(), "data": {"health": {"reconcile_ok": False, "last_reconcile_ts": iso_now()}}}, ex=60)
                    note_error(env, "reconcile", e)

            # ------------------------------------------------------------
            # 3) Poll orderStatus for active oids
            #    ✅ state.events cycle_id should come from report-cycle mapping when available,
            #      otherwise fall back to current_cycle_id()
            # ------------------------------------------------------------
            if ACCOUNT and (now - last_status_ts) >= STATUS_POLL_SECONDS:
                last_status_ts = now
                try:
                    oids: Set[str] = set(bus.r.smembers(ACTIVE_OIDS_SET)) # type: ignore
                    for oid_s in list(oids)[:200]:
                        try:
                            oid = int(oid_s)
                        except Exception:
                            bus.r.srem(ACTIVE_OIDS_SET, oid_s)
                            bus.r.hdel(OID_CYCLE_MAP, oid_s)
                            continue

                        # try to get original cycle_id from mapping
                        mapped_cycle = bus.r.hget(OID_CYCLE_MAP, oid_s)
                        if mapped_cycle:
                            env = Envelope(source=SERVICE, cycle_id=mapped_cycle) # type: ignore
                        else:
                            env = Envelope(source=SERVICE, cycle_id=current_cycle_id())

                        try:
                            stj = order_status(client, HL_HTTP_URL, ACCOUNT, oid)
                            bus.xadd_json(
                                STREAM_EVENTS,
                                require_env({"env": env.model_dump(), "event": "orderStatus", "oid": oid, "data": stj}),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_EVENTS).inc()

                            if _terminal_status(stj):
                                bus.r.srem(ACTIVE_OIDS_SET, oid_s)
                                bus.r.hdel(OID_CYCLE_MAP, oid_s)
                        except Exception as e:
                            # keep in set; retry later
                            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "where": "orderStatus", "err": str(e), "oid": oid}))
                            note_error(env, "orderStatus", e)
                            continue
                except Exception as e:
                    env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "where": "orderStatus_outer", "err": str(e)}))
                    note_error(env, "orderStatus_outer", e)

            time.sleep(0.2)
            LAT.labels(SERVICE, "loop").observe(0.2)

if __name__ == "__main__":
    main()
