import os
import time
from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import Envelope, TargetPortfolio, StateSnapshot, ApprovedTargetPortfolio, TargetWeight, Rejection, current_cycle_id
from pydantic import ValidationError
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

STREAM_IN = "alpha.target"
STREAM_STATE_KEY = "latest.state.snapshot"  # Redis key maintained by portfolio_state
STREAM_OUT = "risk.approved"
AUDIT = "audit.logs"

GROUP = "risk_grp"
CONSUMER = os.environ.get("CONSUMER", "risk_1")
RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))

SERVICE = "risk_engine"
os.environ["SERVICE_NAME"] = SERVICE

def cap_for(sym: str) -> float:
    if sym in ("BTC", "ETH"):
        return float(os.environ.get("CAP_BTC_ETH", "0.15"))
    return float(os.environ.get("CAP_ALT", "0.10"))

def main():
    start_metrics("METRICS_PORT", 9104)
    bus = RedisStreams(REDIS_URL)
    max_gross = float(os.environ.get("MAX_GROSS", "0.40"))
    max_net = float(os.environ.get("MAX_NET", "0.20"))
    turnover_cap = float(os.environ.get("TURNOVER_CAP", "0.10"))
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

    while True:
        msgs = bus.xreadgroup_json(STREAM_IN, GROUP, CONSUMER, count=20, block_ms=5000)
        for stream, msg_id, payload in msgs:
            MSG_IN.labels(SERVICE, stream).inc()
            t0 = time.time()
            try:
                try:
                    payload = require_env(payload)
                    incoming_env = Envelope(**payload["env"])
                except (KeyError, ValidationError, ValueError) as e:
                    env = Envelope(source="risk_engine", cycle_id=current_cycle_id())
                    dlq = {
                        "env": env.model_dump(),
                        "event": "protocol_error",
                        "reason": str(e),
                        "data": payload,
                    }
                    bus.xadd_json(f"dlq.{stream}", dlq)
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "protocol_error", "reason": str(e), "data": payload}))
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    ERR.labels(SERVICE, "protocol").inc()
                    note_error(env, "protocol", e)
                    continue

                try:
                    tp = TargetPortfolio(**payload["data"])
                except ValidationError as e:
                    env = Envelope(source="risk_engine", cycle_id=incoming_env.cycle_id)
                    dlq = {
                        "env": env.model_dump(),
                        "event": "schema_error",
                        "schema": "TargetPortfolio",
                        "reason": str(e),
                        "data": payload.get("data"),
                    }
                    bus.xadd_json(f"dlq.{stream}", dlq)
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "schema_error", "schema": "TargetPortfolio", "reason": str(e), "data": payload.get("data")}))
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    ERR.labels(SERVICE, "schema").inc()
                    note_error(env, "schema", e)
                    continue

                # load latest state
                st_raw = bus.get_json(STREAM_STATE_KEY)
                st = None
                if not st_raw:
                    mode = "HALT"
                else:
                    try:
                        st = StateSnapshot(**st_raw["data"])
                        mode = "NORMAL"
                    except ValidationError as e:
                        env = Envelope(source="risk_engine", cycle_id=incoming_env.cycle_id)
                        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "schema_error", "schema": "StateSnapshot", "reason": str(e), "data": st_raw.get("data")}))
                        ERR.labels(SERVICE, "schema").inc()
                        note_error(env, "schema", e)
                        mode = "HALT"

                rejections = []
                approved = []
                # 1) per-symbol cap
                for tw in tp.targets:
                    cap = cap_for(tw.symbol)
                    w0 = tw.weight
                    w1 = max(min(w0, cap), -cap)
                    if abs(w1 - w0) > 1e-12:
                        rejections.append(Rejection(symbol=tw.symbol, reason="per_symbol_cap", original_weight=w0, approved_weight=w1))
                    approved.append(TargetWeight(symbol=tw.symbol, weight=w1))

                gross = sum(abs(x.weight) for x in approved)
                net = sum(x.weight for x in approved)

                # 2) gross/net caps
                scale = 1.0
                if gross > max_gross + 1e-12:
                    scale = max_gross / gross
                if abs(net) > max_net + 1e-12:
                    # scale towards net bound too (conservative)
                    scale = min(scale, max_net / abs(net))

                if scale < 0.999:
                    for i, x in enumerate(approved):
                        approved[i] = TargetWeight(symbol=x.symbol, weight=x.weight * scale)
                    rejections.append(Rejection(symbol="*", reason="gross_or_net_scaled", original_weight=gross, approved_weight=sum(abs(x.weight) for x in approved)))

                # 3) turnover cap (requires current weights; approximate using positions notional / equity)
                if st:
                    # compute current weights approximately
                    eq = max(st.equity_usd, 1e-6)
                    cur = {}
                    for sym in UNIVERSE:
                        pos = st.positions.get(sym)
                        if not pos:
                            cur[sym] = 0.0
                        else:
                            cur[sym] = (pos.qty * pos.mark_px) / eq
                    tgt = {x.symbol: x.weight for x in approved}
                    turnover = sum(abs(tgt.get(sym, 0.0) - cur.get(sym, 0.0)) for sym in UNIVERSE)
                    if turnover > turnover_cap + 1e-12:
                        # scale deltas, not absolute targets (simple conservative method)
                        factor = turnover_cap / turnover
                        for i, x in enumerate(approved):
                            sym = x.symbol
                            w = cur.get(sym, 0.0) + (tgt.get(sym, 0.0) - cur.get(sym, 0.0)) * factor
                            approved[i] = TargetWeight(symbol=sym, weight=w)
                        rejections.append(Rejection(symbol="*", reason="turnover_scaled", original_weight=turnover, approved_weight=turnover_cap))

                # TODO: DD / anomaly flags (hook in from state.health / audit)
                ap = ApprovedTargetPortfolio(
                    asof_minute=tp.asof_minute,
                    mode=mode,
                    approved_targets=approved,
                    rejections=rejections,
                    risk_summary={"gross": sum(abs(x.weight) for x in approved),
                                  "net": sum(x.weight for x in approved)},
                )
                env = Envelope(source="risk_engine", cycle_id=incoming_env.cycle_id)
                bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": ap.model_dump()}))
                bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "risk.approved", "data": ap.model_dump()}))
                MSG_OUT.labels(SERVICE, STREAM_OUT).inc()
                MSG_OUT.labels(SERVICE, AUDIT).inc()
                bus.xack(STREAM_IN, GROUP, msg_id)
                note_ok(env)
                LAT.labels(SERVICE, "cycle").observe(time.time() - t0)
            except Exception as e:
                env = Envelope(source="risk_engine", cycle_id=current_cycle_id())
                retry_or_dlq(bus, STREAM_IN, payload, env.model_dump(), RETRY, reason=str(e))
                bus.xack(STREAM_IN, GROUP, msg_id)
                note_error(env, "handler", e)

if __name__ == "__main__":
    main()
