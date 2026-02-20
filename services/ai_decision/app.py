import os
import time
from typing import Dict, List, Tuple

from pydantic import ValidationError

from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import (
    Envelope,
    FeatureSnapshot1m,
    TargetPortfolio,
    TargetWeight,
    current_cycle_id,
)
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

STREAM_IN = "md.features.1m"
STREAM_OUT = "alpha.target"
AUDIT = "audit.logs"
STATE_KEY = "latest.state.snapshot"
LATEST_ALPHA_KEY = "latest.alpha.target"

GROUP = "ai_grp"
CONSUMER = os.environ.get("CONSUMER", "ai_1")
RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))

MAX_GROSS = float(os.environ.get("MAX_GROSS", "0.40"))
AI_SMOOTH_ALPHA = float(os.environ.get("AI_SMOOTH_ALPHA", "0.30"))
AI_MIN_CONFIDENCE = float(os.environ.get("AI_MIN_CONFIDENCE", "0.45"))
AI_TURNOVER_CAP = float(os.environ.get("AI_TURNOVER_CAP", os.environ.get("TURNOVER_CAP", "0.10")))

SERVICE = "ai_decision"
os.environ["SERVICE_NAME"] = SERVICE


def normalize(weights: Dict[str, float]) -> Dict[str, float]:
    s = sum(max(w, 0.0) for w in weights.values())
    if s <= 1e-12:
        return {k: 0.0 for k in weights}
    return {k: max(v, 0.0) / s for k, v in weights.items()}


def confidence_from_signals(raw: Dict[str, float], universe: List[str]) -> float:
    if not universe:
        return 0.0
    positives = [max(raw.get(sym, 0.0), 0.0) for sym in universe]
    breadth = sum(1 for x in positives if x > 0) / float(len(universe))
    total = sum(positives)
    strength = total / (total + 1.0)
    conf = 0.5 * breadth + 0.5 * strength
    return max(0.0, min(1.0, conf))


def scale_gross(weights: Dict[str, float], gross_cap: float) -> Dict[str, float]:
    gross = sum(abs(v) for v in weights.values())
    if gross <= gross_cap + 1e-12:
        return dict(weights)
    if gross <= 1e-12:
        return dict(weights)
    k = gross_cap / gross
    return {sym: w * k for sym, w in weights.items()}


def apply_confidence_gating(weights: Dict[str, float], confidence: float, min_confidence: float) -> Dict[str, float]:
    if min_confidence <= 0:
        return dict(weights)
    if confidence >= min_confidence:
        return dict(weights)
    scale = max(0.0, confidence / min_confidence)
    return {sym: w * scale for sym, w in weights.items()}


def ewma_smooth(new_w: Dict[str, float], prev_w: Dict[str, float], alpha: float, universe: List[str]) -> Dict[str, float]:
    a = max(0.0, min(1.0, alpha))
    out: Dict[str, float] = {}
    for sym in universe:
        out[sym] = (1.0 - a) * prev_w.get(sym, 0.0) + a * new_w.get(sym, 0.0)
    return out


def apply_turnover_cap(target_w: Dict[str, float], current_w: Dict[str, float], cap: float, universe: List[str]) -> Tuple[Dict[str, float], float, float]:
    turnover_before = sum(abs(target_w.get(sym, 0.0) - current_w.get(sym, 0.0)) for sym in universe)
    if cap <= 0:
        return dict(target_w), turnover_before, turnover_before
    if turnover_before <= cap + 1e-12:
        return dict(target_w), turnover_before, turnover_before
    k = cap / turnover_before
    capped = {
        sym: current_w.get(sym, 0.0) + (target_w.get(sym, 0.0) - current_w.get(sym, 0.0)) * k
        for sym in universe
    }
    turnover_after = sum(abs(capped.get(sym, 0.0) - current_w.get(sym, 0.0)) for sym in universe)
    return capped, turnover_before, turnover_after


def weights_from_targets(targets: List[TargetWeight], universe: List[str]) -> Dict[str, float]:
    out = {sym: 0.0 for sym in universe}
    for t in targets:
        if t.symbol in out:
            out[t.symbol] = float(t.weight)
    return out


def targets_from_weights(weights: Dict[str, float], universe: List[str]) -> List[TargetWeight]:
    return [TargetWeight(symbol=sym, weight=float(weights.get(sym, 0.0))) for sym in universe]


def get_prev_weights(bus: RedisStreams, universe: List[str]) -> Dict[str, float]:
    raw = bus.get_json(LATEST_ALPHA_KEY)
    if not raw or "data" not in raw:
        return {sym: 0.0 for sym in universe}
    try:
        tp = TargetPortfolio(**raw["data"])
    except Exception:
        return {sym: 0.0 for sym in universe}
    return weights_from_targets(tp.targets, universe)


def get_current_weights(bus: RedisStreams, universe: List[str]) -> Dict[str, float]:
    raw = bus.get_json(STATE_KEY)
    if not raw or "data" not in raw:
        return {sym: 0.0 for sym in universe}
    data = raw.get("data", {})
    equity = float(data.get("equity_usd", 0.0) or 0.0)
    if equity <= 1e-9:
        return {sym: 0.0 for sym in universe}
    positions = data.get("positions", {}) or {}
    out = {sym: 0.0 for sym in universe}
    for sym in universe:
        pos = positions.get(sym)
        if not isinstance(pos, dict):
            continue
        qty = float(pos.get("qty", 0.0) or 0.0)
        mark = float(pos.get("mark_px", 0.0) or 0.0)
        out[sym] = (qty * mark) / equity
    return out


def build_baseline_weights(fs: FeatureSnapshot1m, universe: List[str], max_gross: float) -> Dict[str, float]:
    raw = {}
    for sym in universe:
        m = fs.ret_5m.get(sym, 0.0)
        vol = max(fs.vol_1h.get(sym, 0.01), 0.001)
        raw[sym] = max(m, 0.0) / vol
    return {sym: normalize(raw).get(sym, 0.0) * max_gross for sym in universe}


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

                # 1) baseline target from mom/vol
                target_w = build_baseline_weights(fs, UNIVERSE, MAX_GROSS)
                raw = {
                    sym: max(fs.ret_5m.get(sym, 0.0), 0.0) / max(fs.vol_1h.get(sym, 0.01), 0.001)
                    for sym in UNIVERSE
                }

                # 2) confidence gating
                confidence = confidence_from_signals(raw, UNIVERSE)
                gated_w = apply_confidence_gating(target_w, confidence, AI_MIN_CONFIDENCE)

                # 3) ewma smoothing against previous target
                prev_w = get_prev_weights(bus, UNIVERSE)
                smooth_w = ewma_smooth(gated_w, prev_w, AI_SMOOTH_ALPHA, UNIVERSE)

                # 4) turnover cap against current portfolio snapshot
                current_w = get_current_weights(bus, UNIVERSE)
                capped_w, turnover_before, turnover_after = apply_turnover_cap(smooth_w, current_w, AI_TURNOVER_CAP, UNIVERSE)

                # final gross clamp
                final_w = scale_gross(capped_w, MAX_GROSS)
                gross = sum(abs(v) for v in final_w.values())

                tp = TargetPortfolio(
                    asof_minute=fs.asof_minute,
                    universe=UNIVERSE,
                    targets=targets_from_weights(final_w, UNIVERSE),
                    cash_weight=max(0.0, 1.0 - gross),
                    confidence=confidence,
                    rationale="baseline mom/vol + confidence gating + EWMA smoothing + turnover cap",
                    model={"name": "baseline_stabilized", "version": "v2"},
                    constraints_hint={"max_gross": MAX_GROSS, "turnover_cap": AI_TURNOVER_CAP},
                )

                env = Envelope(source="ai_decision", cycle_id=incoming_env.cycle_id)
                bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": tp.model_dump()}))
                bus.set_json(LATEST_ALPHA_KEY, {"env": env.model_dump(), "data": tp.model_dump()}, ex=3600)
                bus.xadd_json(
                    AUDIT,
                    require_env(
                        {
                            "env": env.model_dump(),
                            "event": "alpha.target",
                            "data": {
                                "used": "baseline_stabilized",
                                "confidence": confidence,
                                "gross": gross,
                                "turnover_before": turnover_before,
                                "turnover_after": turnover_after,
                                "smooth_alpha": AI_SMOOTH_ALPHA,
                                "confidence_threshold": AI_MIN_CONFIDENCE,
                                "turnover_cap": AI_TURNOVER_CAP,
                            },
                        }
                    ),
                )
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
