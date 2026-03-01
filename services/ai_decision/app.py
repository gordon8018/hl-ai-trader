import os
import time
import json
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any

from pydantic import ValidationError

from shared.ai.llm_client import LLMConfig, call_llm_json
from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import (
    Envelope,
    FeatureSnapshot1m,
    FeatureSnapshot15m,
    TargetPortfolio,
    TargetWeight,
    current_cycle_id,
)
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

STREAM_IN = os.environ.get("AI_STREAM_IN", "md.features.1m")
STREAM_OUT = "alpha.target"
AUDIT = "audit.logs"
STATE_KEY = "latest.state.snapshot"
LATEST_ALPHA_KEY = "latest.alpha.target"
LATEST_MAJOR_TS_KEY = "latest.alpha.major.ts"

GROUP = "ai_grp"
CONSUMER = os.environ.get("CONSUMER", "ai_1")
RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))

MAX_GROSS = float(os.environ.get("MAX_GROSS", "0.40"))
MAX_NET = float(os.environ.get("MAX_NET", "0.20"))
CAP_BTC_ETH = float(os.environ.get("CAP_BTC_ETH", str(MAX_GROSS)))
CAP_ALT = float(os.environ.get("CAP_ALT", str(MAX_GROSS)))
AI_SMOOTH_ALPHA = float(os.environ.get("AI_SMOOTH_ALPHA", "0.30"))
AI_MIN_CONFIDENCE = float(os.environ.get("AI_MIN_CONFIDENCE", "0.45"))
AI_TURNOVER_CAP = float(os.environ.get("AI_TURNOVER_CAP", os.environ.get("TURNOVER_CAP", "0.10")))
AI_DECISION_HORIZON = os.environ.get("AI_DECISION_HORIZON", "15m")
AI_SIGNAL_DELTA_THRESHOLD = float(os.environ.get("AI_SIGNAL_DELTA_THRESHOLD", "0.15"))
AI_MIN_MAJOR_INTERVAL_MIN = int(os.environ.get("AI_MIN_MAJOR_INTERVAL_MIN", "5"))
AI_USE_LLM = os.environ.get("AI_USE_LLM", "false").lower() == "true"
AI_LLM_MOCK_RESPONSE = os.environ.get("AI_LLM_MOCK_RESPONSE", "")
AI_LLM_ENDPOINT = os.environ.get("AI_LLM_ENDPOINT", "").strip()
AI_LLM_API_KEY = os.environ.get("AI_LLM_API_KEY", "").strip()
AI_LLM_MODEL = os.environ.get("AI_LLM_MODEL", "").strip()
AI_LLM_TIMEOUT_MS = int(os.environ.get("AI_LLM_TIMEOUT_MS", "1500"))

SERVICE = "ai_decision"
os.environ["SERVICE_NAME"] = SERVICE

SYSTEM_PROMPT = (
    "You are a portfolio construction engine for crypto perpetual futures. "
    "Output STRICT JSON only with fields: targets, cash_weight, confidence, rationale, evidence. "
    "Prefer stable low-turnover decisions and reduce risk if uncertain."
)


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


def apply_symbol_caps(weights: Dict[str, float], universe: List[str], cap_btc_eth: float, cap_alt: float) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for sym in universe:
        cap = cap_btc_eth if sym in {"BTC", "ETH"} else cap_alt
        w = weights.get(sym, 0.0)
        if cap <= 0:
            out[sym] = 0.0
        else:
            out[sym] = max(-cap, min(cap, w))
    return out


def apply_net_cap(weights: Dict[str, float], max_net: float) -> Tuple[Dict[str, float], float, float]:
    net_before = sum(weights.values())
    if max_net <= 0:
        return dict(weights), net_before, net_before
    if abs(net_before) <= max_net + 1e-12:
        return dict(weights), net_before, net_before
    if abs(net_before) <= 1e-12:
        return dict(weights), net_before, net_before
    k = max_net / abs(net_before)
    scaled = {sym: w * k for sym, w in weights.items()}
    net_after = sum(scaled.values())
    return scaled, net_before, net_after


def apply_cash_weight(weights: Dict[str, float], cash_weight: Optional[float], max_gross: float) -> Tuple[Dict[str, float], Optional[float], Optional[float]]:
    if cash_weight is None:
        return dict(weights), None, None
    cw = max(0.0, min(1.0, float(cash_weight)))
    target_gross = min(max_gross, max(0.0, 1.0 - cw))
    gross = sum(abs(v) for v in weights.values())
    if gross <= target_gross + 1e-12:
        return dict(weights), cw, target_gross
    if gross <= 1e-12:
        return dict(weights), cw, target_gross
    k = target_gross / gross
    return {sym: w * k for sym, w in weights.items()}, cw, target_gross


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


def get_prev_target(bus: RedisStreams, universe: List[str]) -> Tuple[Dict[str, float], Optional[str]]:
    raw = bus.get_json(LATEST_ALPHA_KEY)
    if not raw or "data" not in raw:
        return {sym: 0.0 for sym in universe}, None
    try:
        tp = TargetPortfolio(**raw["data"])
        return weights_from_targets(tp.targets, universe), tp.major_cycle_id
    except Exception:
        return {sym: 0.0 for sym in universe}, None


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


def _extract_json_object(raw_text: str) -> str:
    s = str(raw_text or "").strip()
    if not s:
        raise ValueError("empty_llm_output")
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("json_object_not_found")
    return s[start : end + 1]


def parse_llm_weights_with_evidence(raw_text: str, universe: List[str], max_gross: float) -> Tuple[Dict[str, float], float, str, Optional[float], Dict[str, Any]]:
    obj = json.loads(_extract_json_object(raw_text))
    if not isinstance(obj, dict):
        raise ValueError("llm_json_not_object")

    raw_targets = obj.get("targets")
    # Accept dict format {"BTC": 0.1, ...} and convert to list format
    if isinstance(raw_targets, dict):
        raw_targets = [{"symbol": k, "weight": v} for k, v in raw_targets.items()]
    if not isinstance(raw_targets, list):
        raise ValueError("llm_targets_missing")

    out = {sym: 0.0 for sym in universe}
    for t in raw_targets:
        if not isinstance(t, dict):
            continue
        sym = t.get("symbol")
        w = t.get("weight")
        if not isinstance(sym, str) or sym not in out:
            continue
        if not isinstance(w, (int, float)):
            continue
        out[sym] = float(w)

    conf = obj.get("confidence", 0.5)
    confidence = float(conf) if isinstance(conf, (int, float)) else 0.5
    confidence = max(0.0, min(1.0, confidence))
    rationale = str(obj.get("rationale", "llm_json"))
    cash_weight = obj.get("cash_weight") if isinstance(obj.get("cash_weight"), (int, float)) else None
    evidence = obj.get("evidence") if isinstance(obj.get("evidence"), dict) else {}
    return scale_gross(out, max_gross), confidence, rationale, cash_weight, evidence


def parse_llm_weights_strict(raw_text: str, universe: List[str], max_gross: float) -> Tuple[Dict[str, float], float, str, Optional[float]]:
    w, c, r, cw, _ = parse_llm_weights_with_evidence(raw_text, universe, max_gross)
    return w, c, r, cw


def to_major_cycle_id(asof_minute: str) -> str:
    s = asof_minute.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    mm = (dt.minute // 15) * 15
    dt = dt.replace(minute=mm, second=0, microsecond=0)
    return dt.strftime("%Y%m%dT%H%MZ")


def parse_feature(payload_data: Dict[str, Any]) -> FeatureSnapshot15m:
    try:
        return FeatureSnapshot15m(**payload_data)
    except ValidationError:
        fs1 = FeatureSnapshot1m(**payload_data)
        return FeatureSnapshot15m(
            asof_minute=fs1.asof_minute,
            window_start_minute=fs1.asof_minute,
            universe=fs1.universe,
            mid_px=fs1.mid_px,
            ret_15m=fs1.ret_5m,
            ret_30m=fs1.ret_1h,
            ret_1h=fs1.ret_1h,
            vol_15m=fs1.vol_1h,
            vol_1h=fs1.vol_1h,
            spread_bps=fs1.spread_bps,
            liquidity_score=fs1.liquidity_score,
        )


def build_baseline_weights(fs: FeatureSnapshot15m, universe: List[str], max_gross: float) -> Dict[str, float]:
    raw = {}
    for sym in universe:
        m = fs.ret_15m.get(sym, 0.0)
        vol = max(fs.vol_15m.get(sym, 0.01), 0.001)
        raw[sym] = max(m, 0.0) / vol
    return {sym: normalize(raw).get(sym, 0.0) * max_gross for sym in universe}


def should_rebalance(bus: RedisStreams, prev_w: Dict[str, float], candidate_w: Dict[str, float], asof_minute: str) -> Tuple[bool, float, str]:
    delta = sum(abs(candidate_w.get(sym, 0.0) - prev_w.get(sym, 0.0)) for sym in UNIVERSE)
    if delta < AI_SIGNAL_DELTA_THRESHOLD:
        return False, delta, "signal_delta_below_threshold"

    raw = bus.get_json(LATEST_MAJOR_TS_KEY)
    if raw and isinstance(raw, dict):
        last_ts = raw.get("ts")
        if isinstance(last_ts, (int, float)):
            now_dt = datetime.fromisoformat(asof_minute.replace("Z", "+00:00"))
            now_ts = now_dt.replace(second=0, microsecond=0).timestamp()
            if (now_ts - float(last_ts)) < (AI_MIN_MAJOR_INTERVAL_MIN * 60):
                return False, delta, "min_major_interval"

    return True, delta, "major_rebalance"


def build_user_payload(
    fs: FeatureSnapshot15m,
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
) -> Dict[str, Any]:
    reject_avg = sum(float(fs.reject_rate_15m.get(sym, 0.0)) for sym in UNIVERSE) / max(len(UNIVERSE), 1)
    p95_avg = sum(float(fs.p95_latency_ms_15m.get(sym, 0.0)) for sym in UNIVERSE) / max(len(UNIVERSE), 1)
    slippage_avg = sum(float(fs.slippage_bps_15m.get(sym, 0.0)) for sym in UNIVERSE) / max(len(UNIVERSE), 1)
    return {
        "task": "Propose target portfolio weights for the next 15-minute horizon.",
        "universe": UNIVERSE,
        "constraints": {
            "max_gross": MAX_GROSS,
            "turnover_cap_per_cycle": AI_TURNOVER_CAP,
            "prefer_low_turnover": True,
        },
        "market_features_15m": {
            "asof_minute": fs.asof_minute,
            "window_start_minute": fs.window_start_minute,
            "ret_15m": fs.ret_15m,
            "ret_30m": fs.ret_30m,
            "ret_1h": fs.ret_1h,
            "vol_15m": fs.vol_15m,
            "vol_1h": fs.vol_1h,
            "funding_rate": fs.funding_rate,
            "basis_bps": fs.basis_bps,
            "open_interest": fs.open_interest,
            "oi_change_15m": fs.oi_change_15m,
            "spread_bps": fs.spread_bps,
            "book_imbalance_l1": fs.book_imbalance_l1,
            "book_imbalance_l5": fs.book_imbalance_l5,
            "top_depth_usd": fs.top_depth_usd,
            "microprice": fs.microprice,
            "liquidity_score": fs.liquidity_score,
        },
        "execution_feedback_15m": {
            "reject_rate_by_symbol": fs.reject_rate_15m,
            "p95_latency_ms_by_symbol": fs.p95_latency_ms_15m,
            "slippage_bps_by_symbol": fs.slippage_bps_15m,
            "reject_rate_avg": reject_avg,
            "p95_latency_ms_avg": p95_avg,
            "slippage_bps_avg": slippage_avg,
        },
        "portfolio_state": {
            "current_weights": current_w,
            "prev_target_weights": prev_w,
        },
        "output_schema": {
            "required": ["targets", "cash_weight", "confidence", "rationale", "evidence"]
        },
    }


def maybe_llm_candidate_weights(
    *,
    use_llm: bool,
    llm_raw_response: str,
    universe: List[str],
    max_gross: float,
) -> Tuple[Optional[Dict[str, float]], Optional[float], Optional[str], Optional[float], Optional[str], Optional[str]]:
    if not use_llm:
        return None, None, None, None, None, None
    if not llm_raw_response.strip():
        return None, None, None, None, None, "llm_empty_response"
    try:
        w, c, r, cw = parse_llm_weights_strict(llm_raw_response, universe, max_gross)
        return w, c, r, cw, llm_raw_response, None
    except Exception as e:
        return None, None, None, None, llm_raw_response, str(e)


def maybe_llm_candidate_weights_online(
    fs: FeatureSnapshot15m,
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
) -> Tuple[Optional[Dict[str, float]], Optional[float], Optional[str], Optional[float], Optional[str], Optional[str], Dict[str, Any], Dict[str, Any]]:
    llm_meta: Dict[str, Any] = {"provider": "none"}
    evidence: Dict[str, Any] = {}
    if not AI_USE_LLM:
        return None, None, None, None, None, None, llm_meta, evidence

    raw = AI_LLM_MOCK_RESPONSE
    if AI_LLM_ENDPOINT and AI_LLM_API_KEY and AI_LLM_MODEL:
        cfg = LLMConfig(
            endpoint=AI_LLM_ENDPOINT,
            api_key=AI_LLM_API_KEY,
            model=AI_LLM_MODEL,
            timeout_ms=AI_LLM_TIMEOUT_MS,
            temperature=0.1,
        )
        user_payload = build_user_payload(fs, current_w, prev_w)
        raw, llm_meta, call_err = call_llm_json(cfg, system_prompt=SYSTEM_PROMPT, user_payload=user_payload)
        llm_meta["provider"] = "online"
        if call_err:
            return None, None, None, None, raw, call_err, llm_meta, evidence
    else:
        llm_meta["provider"] = "mock"

    if not raw or not raw.strip():
        return None, None, None, None, raw, "llm_empty_response", llm_meta, evidence
    try:
        w, c, r, cw, evidence = parse_llm_weights_with_evidence(raw, UNIVERSE, MAX_GROSS)
        llm_meta["parse_ok"] = True
        return w, c, r, cw, raw, None, llm_meta, evidence
    except Exception as e:
        llm_meta["parse_ok"] = False
        return None, None, None, None, raw, str(e), llm_meta, evidence


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
                    fs = parse_feature(payload["data"])
                except ValidationError as e:
                    env = Envelope(source="ai_decision", cycle_id=incoming_env.cycle_id)
                    dlq = {
                        "env": env.model_dump(),
                        "event": "schema_error",
                        "schema": "FeatureSnapshot15m",
                        "reason": str(e),
                        "data": payload.get("data"),
                    }
                    bus.xadd_json(f"dlq.{stream}", dlq)
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "schema_error", "schema": "FeatureSnapshot15m", "reason": str(e), "data": payload.get("data")}))
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    ERR.labels(SERVICE, "schema").inc()
                    note_error(env, "schema", e)
                    continue

                baseline_w = build_baseline_weights(fs, UNIVERSE, MAX_GROSS)
                raw_signal = {
                    sym: max(fs.ret_15m.get(sym, 0.0), 0.0) / max(fs.vol_15m.get(sym, 0.01), 0.001)
                    for sym in UNIVERSE
                }
                confidence = confidence_from_signals(raw_signal, UNIVERSE)
                used = "baseline_stabilized"
                raw_llm = None
                llm_meta: Dict[str, Any] = {}
                llm_evidence: Dict[str, Any] = {}

                prev_w, _ = get_prev_target(bus, UNIVERSE)
                current_w = get_current_weights(bus, UNIVERSE)

                target_w = baseline_w
                llm_parse_error = None
                llm_w, llm_conf, llm_rationale, llm_cash_weight, llm_raw, llm_parse_error, llm_meta, llm_evidence = maybe_llm_candidate_weights_online(
                    fs, current_w, prev_w
                )
                if llm_w is not None:
                    target_w = llm_w
                    confidence = llm_conf if llm_conf is not None else confidence
                    used = "llm_strict_json"
                    raw_llm = llm_raw
                    rationale = llm_rationale or "llm_strict_json"
                elif AI_USE_LLM:
                    used = "baseline_fallback"
                    rationale = "baseline fallback due to llm strict-json failure"
                    raw_llm = llm_raw
                else:
                    rationale = "baseline 15m mom/vol + confidence gating + EWMA smoothing + turnover cap"

                capped_symbol_w = apply_symbol_caps(target_w, UNIVERSE, CAP_BTC_ETH, CAP_ALT)
                cash_adjusted_w, cash_weight_in, cash_gross_target = apply_cash_weight(capped_symbol_w, llm_cash_weight, MAX_GROSS)
                gross_capped_w = scale_gross(cash_adjusted_w, MAX_GROSS)
                gated_w = apply_confidence_gating(gross_capped_w, confidence, AI_MIN_CONFIDENCE)
                smooth_w = ewma_smooth(gated_w, prev_w, AI_SMOOTH_ALPHA, UNIVERSE)
                capped_w, turnover_before, turnover_after = apply_turnover_cap(smooth_w, current_w, AI_TURNOVER_CAP, UNIVERSE)
                candidate_w, net_before, net_after = apply_net_cap(capped_w, MAX_NET)

                rebalance, signal_delta, action_reason = should_rebalance(bus, prev_w, candidate_w, fs.asof_minute)
                final_w = candidate_w if rebalance else prev_w
                decision_action = "REBALANCE" if rebalance else "HOLD"

                gross = sum(abs(v) for v in final_w.values())
                major_cycle = to_major_cycle_id(fs.asof_minute)
                evidence = {
                    "regime": "short_term_15m",
                    "signals": {
                        "signal_delta": signal_delta,
                        "ret_15m": fs.ret_15m,
                        "vol_15m": fs.vol_15m,
                        "funding_rate": fs.funding_rate,
                        "basis_bps": fs.basis_bps,
                        "oi_change_15m": fs.oi_change_15m,
                        "spread_bps": fs.spread_bps,
                        "book_imbalance_l1": fs.book_imbalance_l1,
                        "book_imbalance_l5": fs.book_imbalance_l5,
                    },
                    "constraint_actions": {
                        "turnover_before": turnover_before,
                        "turnover_after": turnover_after,
                        "net_before": net_before,
                        "net_after": net_after,
                        "cash_weight_in": cash_weight_in,
                        "cash_gross_target": cash_gross_target,
                        "cap_btc_eth": CAP_BTC_ETH,
                        "cap_alt": CAP_ALT,
                    },
                    "execution_feedback_15m": {
                        "reject_rate_by_symbol": fs.reject_rate_15m,
                        "p95_latency_ms_by_symbol": fs.p95_latency_ms_15m,
                        "slippage_bps_by_symbol": fs.slippage_bps_15m,
                    },
                    "risk_flags": [],
                    "llm_meta": llm_meta,
                    "llm_evidence": llm_evidence,
                    "decision_reason": action_reason,
                }

                tp = TargetPortfolio(
                    asof_minute=fs.asof_minute,
                    universe=UNIVERSE,
                    targets=targets_from_weights(final_w, UNIVERSE),
                    cash_weight=max(0.0, 1.0 - gross),
                    confidence=confidence,
                    rationale=rationale,
                    model={"name": used, "version": "v3"},
                    constraints_hint={
                        "max_gross": MAX_GROSS,
                        "max_net": MAX_NET,
                        "cap_btc_eth": CAP_BTC_ETH,
                        "cap_alt": CAP_ALT,
                        "turnover_cap": AI_TURNOVER_CAP,
                    },
                    decision_horizon=AI_DECISION_HORIZON,
                    major_cycle_id=major_cycle,
                    decision_action=decision_action,
                    evidence=evidence,
                )

                env = Envelope(source="ai_decision", cycle_id=incoming_env.cycle_id)
                bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": tp.model_dump()}))
                bus.set_json(LATEST_ALPHA_KEY, {"env": env.model_dump(), "data": tp.model_dump()}, ex=3600)
                if rebalance:
                    now_dt = datetime.fromisoformat(fs.asof_minute.replace("Z", "+00:00"))
                    bus.set_json(LATEST_MAJOR_TS_KEY, {"ts": now_dt.timestamp(), "cycle_id": major_cycle}, ex=3600)
                audit_event = "ai.major_decision" if rebalance else "ai.hold_decision"
                bus.xadd_json(
                    AUDIT,
                    require_env(
                        {
                            "env": env.model_dump(),
                            "event": audit_event,
                            "data": {
                                "used": used,
                                "decision_action": decision_action,
                                "raw_llm": raw_llm,
                                "llm_parse_error": llm_parse_error,
                                "confidence": confidence,
                                "gross": gross,
                                "net_before": net_before,
                                "net_after": net_after,
                                "cash_weight_in": cash_weight_in,
                                "turnover_before": turnover_before,
                                "turnover_after": turnover_after,
                                "smooth_alpha": AI_SMOOTH_ALPHA,
                                "confidence_threshold": AI_MIN_CONFIDENCE,
                                "turnover_cap": AI_TURNOVER_CAP,
                                "signal_delta": signal_delta,
                                "decision_reason": action_reason,
                                "reject_rate_15m": fs.reject_rate_15m,
                                "p95_latency_ms_15m": fs.p95_latency_ms_15m,
                                "slippage_bps_15m": fs.slippage_bps_15m,
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
