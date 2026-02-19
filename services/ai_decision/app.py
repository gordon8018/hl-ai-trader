import os
import time
from shared.bus import publish
from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import Envelope, FeatureSnapshot1m, TargetPortfolio, TargetWeight, current_cycle_id
from typing import List
from pydantic import ValidationError
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

STREAM_IN = "md.features.1m"
STREAM_OUT = "alpha.target"
AUDIT = "audit.logs"

GROUP = "ai_grp"
CONSUMER = os.environ.get("CONSUMER", "ai_1")
RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))

SERVICE = "ai_decision"
os.environ["SERVICE_NAME"] = SERVICE

def normalize(weights):
    s = sum(max(w, 0.0) for w in weights.values())
    if s <= 1e-12:
        return {k: 0.0 for k in weights}
    return {k: max(v, 0.0) / s for k, v in weights.items()}

def main():
    start_metrics("METRICS_PORT", 9103)
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

    while True:
        msgs = bus.xreadgroup_json(STREAM_IN, GROUP, CONSUMER, count=10, block_ms=5000)
        for stream, msg_id, payload in msgs:
            MSG_IN.labels(SERVICE, stream).inc()
            t0 = time.time()
            try:
                try:
                    payload = require_env(payload)
                    incoming_env = Envelope(**payload["env"])
                except (KeyError, ValidationError, ValueError) as e:
                    env = Envelope(source="ai_decision", cycle_id=current_cycle_id())
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
                    fs = FeatureSnapshot1m(**payload["data"])
                except ValidationError as e:
                    env = Envelope(source="ai_decision", cycle_id=incoming_env.cycle_id)
                    dlq = {
                        "env": env.model_dump(),
                        "event": "schema_error",
                        "schema": "FeatureSnapshot1m",
                        "reason": str(e),
                        "data": payload.get("data"),
                    }
                    bus.xadd_json(f"dlq.{stream}", dlq)
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "schema_error", "schema": "FeatureSnapshot1m", "reason": str(e), "data": payload.get("data")}))
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    ERR.labels(SERVICE, "schema").inc()
                    note_error(env, "schema", e)
                    continue
                # baseline: allocate to positive 5m momentum inversely weighted by 1h vol
                raw = {}
                for sym in UNIVERSE:
                    m = fs.ret_5m.get(sym, 0.0)
                    vol = max(fs.vol_1h.get(sym, 0.01), 0.001)
                    raw[sym] = max(m, 0.0) / vol

                w = normalize(raw)

                # conservative gross cap in baseline
                max_gross = float(os.environ.get("MAX_GROSS", "0.40"))
                targets: List[TargetWeight] = []
                gross = 0.0
                for sym in UNIVERSE:
                    ww = w.get(sym, 0.0) * max_gross
                    gross += abs(ww)
                    targets.append(TargetWeight(symbol=sym, weight=ww))

                tp = TargetPortfolio(
                    asof_minute=fs.asof_minute,
                    universe=UNIVERSE,
                    targets=targets,
                    cash_weight=max(0.0, 1.0 - gross),
                    confidence=0.4,
                    rationale="baseline: positive momentum / vol scaling",
                    model={"name": "baseline_momvol", "version": "v1"},
                    constraints_hint={"max_gross": max_gross},
                )

                env = Envelope(source="ai_decision", cycle_id=incoming_env.cycle_id)
                bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": tp.model_dump()}))
                bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "alpha.target", "data": tp.model_dump()}))
                MSG_OUT.labels(SERVICE, STREAM_OUT).inc()
                MSG_OUT.labels(SERVICE, AUDIT).inc()

                bus.xack(STREAM_IN, GROUP, msg_id)
                note_ok(env)
                LAT.labels(SERVICE, "cycle").observe(time.time() - t0)
            except Exception as e:
                env = Envelope(source="ai_decision", cycle_id=current_cycle_id())
                retry_or_dlq(bus, STREAM_IN, payload, env.model_dump(), RETRY, reason=str(e))
                bus.xack(STREAM_IN, GROUP, msg_id)
                note_error(env, "handler", e)

if __name__ == "__main__":
    main()
