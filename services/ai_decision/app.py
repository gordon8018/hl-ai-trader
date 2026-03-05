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
from shared.metrics.prom import (
    start_metrics,
    MSG_IN,
    MSG_OUT,
    ERR,
    LAT,
    set_alarm,
    LLM_CALLS,
    LLM_ERRORS,
    LLM_LAT_MS,
    AI_FALLBACK,
    AI_CONFIDENCE,
    AI_GROSS,
    AI_NET,
    AI_TURNOVER,
)

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

# High-confidence aggressive mode
AI_CONFIDENCE_HIGH_THRESHOLD = float(os.environ.get("AI_CONFIDENCE_HIGH_THRESHOLD", "0.70"))
MAX_GROSS_HIGH = float(os.environ.get("MAX_GROSS_HIGH", "0.80"))
MAX_NET_HIGH = float(os.environ.get("MAX_NET_HIGH", "0.50"))
AI_TURNOVER_CAP_HIGH = float(os.environ.get("AI_TURNOVER_CAP_HIGH", "0.40"))
AI_SMOOTH_ALPHA_HIGH = float(os.environ.get("AI_SMOOTH_ALPHA_HIGH", "0.70"))
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
    "You are a portfolio construction engine for crypto perpetual futures.\n"
    "ROLE: Convert structured market features into stable, risk-controlled portfolio allocations.\n"
    "OUTPUT: STRICT JSON only. Zero prose outside the JSON object. No markdown.\n\n"

    "=== OUTPUT SCHEMA ===\n"
    "{\"targets\": [{\"symbol\": str, \"weight\": float}],"
    " \"cash_weight\": float,"
    " \"confidence\": float,"
    " \"rationale\": str,"
    " \"evidence\": {\"regime\": str, \"key_signals\": [str], \"risk_flags\": [str]}}\n\n"

    "=== PORTFOLIO CONSTRAINTS ===\n"
    "- Sum(|targets|) + cash_weight = 1\n"
    "- Positive weight = long, negative = short\n"
    "- cash_weight=1.0 means full cash (maximum defensive)\n"
    "- HIGH CONFIDENCE mode (confidence >= 0.70): max_gross up to 0.80, aggressive sizing allowed\n"
    "- NORMAL mode (confidence < 0.70): max_gross <= 0.40, conservative sizing\n"
    "- Constraints are enforced downstream; set targets reflecting your conviction level\n\n"

    "=== FEATURE INTERPRETATION ===\n"
    "TREND & MOMENTUM:\n"
    "- trend_agree=1.0: 15m and 1h agree -> increase directional exposure\n"
    "- trend_agree=0.0: conflicting timeframes -> cut gross, raise cash\n"
    "- trend_strength_15m > 1.5: strong trend, follow it\n"
    "- trend_strength_15m < 0.5: weak/noisy, favor low weights\n"
    "- ret_15m and ret_1h same sign = directional confirmation\n\n"

    "VOLATILITY REGIME (vol_regime):\n"
    "- 0 (low): normal sizing allowed\n"
    "- 1 (normal): standard sizing\n"
    "- 2 (high): reduce gross 30-50%\n"
    "- 3 (very_high): defensive mode, cash_weight > 0.6\n"
    "- vol_spike=1.0: immediately raise cash, cut all exposure\n\n"

    "LIQUIDITY REGIME (liq_regime):\n"
    "- 0 (low): thin book, wide spreads -> reduce sizing, raise cash\n"
    "- 1 (normal): standard sizing\n"
    "- 2 (high): deep book -> can be slightly more aggressive\n"
    "- liquidity_drop=1.0: immediate risk reduction required\n\n"

    "ORDER FLOW & MICROSTRUCTURE:\n"
    "- book_imbalance_l1 > 0.3: strong bid pressure -> short-term bullish\n"
    "- book_imbalance_l1 < -0.3: strong ask pressure -> short-term bearish\n"
    "- volume_imbalance_1m > 0.2: net buying; < -0.2: net selling\n"
    "- aggr_delta_5m > 0: sustained buying 5m -> uptrend; < 0: downtrend\n"
    "- absorption_ratio_bid > 0.7: market absorbs selling -> bullish\n"
    "- absorption_ratio_ask > 0.7: market absorbs buying -> bearish\n"
    "- microprice_change_1m > 0: upward micro-pressure\n\n"

    "PERPETUAL FUTURES SIGNALS:\n"
    "- funding_rate > 0.001: longs pay shorts (crowded long) -> favor short or reduce long\n"
    "- funding_rate < -0.001: shorts pay longs (crowded short) -> favor long or reduce short\n"
    "- basis_bps > 50: contango, bullish sentiment\n"
    "- basis_bps < -50: backwardation, bearish sentiment\n"
    "- oi_change_15m > 0.05: new money entering -> trend confirmation\n"
    "- oi_change_15m < -0.05: positions closing -> potential reversal warning\n\n"

    "RSI (rsi_14_1m):\n"
    "- > 75: overbought -> reduce longs, avoid new longs\n"
    "- < 25: oversold -> reduce shorts, avoid new shorts\n"
    "- 40-60: neutral momentum\n\n"

    "CROSS-MARKET:\n"
    "- corr_btc_1h > 0.8: high BTC correlation, BTC regime drives alt coins\n"
    "- btc_ret_15m negative + corr_btc_1h > 0.7 -> cut alt longs\n"
    "- market_ret_mean_1m direction: broad market bias indicator\n\n"

    "=== EXECUTION FEEDBACK RULES ===\n"
    "- reject_rate_avg > 0.05: execution problems -> cut gross by 50%, raise cash\n"
    "- p95_latency_ms_avg > 500: latency spike -> reduce trading, lower turnover\n"
    "- slippage_bps_avg > 5: high slippage -> reduce all position sizes\n"
    "- Positive deltas (reject_rate_delta, latency_delta, slippage_delta > 0): deteriorating, act now\n"
    "- Any execution problem: raise cash_weight, lower confidence\n\n"

    "=== DECISION FRAMEWORK ===\n"
    "Step 1 - DETECT REGIME:\n"
    "  DEFENSIVE: vol_regime>=2 OR liq_regime=0 OR vol_spike=1 OR liquidity_drop=1\n"
    "    -> cash_weight > 0.6, confidence < 0.5\n"
    "  TRENDING: trend_agree=1 AND vol_regime<=1 AND liq_regime>=1\n"
    "    -> follow ret_15m direction, cash_weight 0.1-0.3\n"
    "  NEUTRAL: conflicting signals\n"
    "    -> cash_weight 0.4-0.6, small directional positions only\n\n"
    "Step 2 - AGGREGATE SIGNALS:\n"
    "  - Require >=2 confirming signals before directional risk\n"
    "  - Use 15m features for direction, 1m for timing, execution for sizing\n"
    "  - Conflicting signals -> reduce gross, raise cash\n\n"
    "Step 3 - SIZE POSITIONS:\n"
    "  HIGH CONFIDENCE (confidence >= 0.70): AGGRESSIVE MODE\n"
    "    - Maximize directional exposure: target gross 0.60-0.80\n"
    "    - cash_weight as low as 0.10-0.20\n"
    "    - Single symbol up to 0.40 weight allowed\n"
    "    - Both long and short positions allowed to maximize return\n"
    "    - Large moves allowed to capture the opportunity quickly\n"
    "  NORMAL (confidence < 0.70):\n"
    "    - Prefer small adjustments from current_weights (low turnover)\n"
    "    - No single symbol > 0.25 weight (concentration risk)\n"
    "    - When multiple symbols show similar signals: distribute evenly\n\n"
    "Step 4 - CALIBRATE CONFIDENCE:\n"
    "  - >= 0.70: HIGH CONVICTION — multiple strong confirming signals, clean execution,\n"
    "             trend_agree=1, vol_regime<=1, liq_regime>=1. Set aggressive weights.\n"
    "  - 0.50-0.69: moderate (some confirming signals). Standard sizing.\n"
    "  - < 0.50: low (conflicting signals). Minimal weights, high cash.\n"
    "  - Set LOW when: vol_spike=1, liquidity_drop=1, trend_agree=0, reject_rate high\n"
    "  - IMPORTANT: be honest about conviction — high confidence unlocks 2x larger positions\n\n"

    "=== ANTI-HALLUCINATION ===\n"
    "- Only use data provided in the user message\n"
    "- If a field is null/zero/missing: treat as neutral, no signal\n"
    "- Do NOT invent price levels, news, or external events\n"
    "- If uncertain: raise cash_weight, lower confidence\n"
    "- Never violate the output schema or portfolio constraints\n"
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
            trade_volume_1m=fs1.trade_volume_1m,
            trade_volume_buy_ratio=fs1.trade_volume_buy_ratio,
            trade_count_1m=fs1.trade_count_1m,
            aggr_buy_volume_1m=fs1.aggr_buy_volume_1m,
            aggr_sell_volume_1m=fs1.aggr_sell_volume_1m,
            aggr_delta_1m=fs1.aggr_delta_1m,
            aggr_delta_5m=fs1.aggr_delta_5m,
            volume_imbalance_1m=fs1.volume_imbalance_1m,
            volume_imbalance_5m=fs1.volume_imbalance_5m,
            buy_pressure_1m=fs1.buy_pressure_1m,
            sell_pressure_1m=fs1.sell_pressure_1m,
            absorption_ratio_bid=fs1.absorption_ratio_bid,
            absorption_ratio_ask=fs1.absorption_ratio_ask,
            microprice_change_1m=fs1.microprice_change_1m,
            book_imbalance_change_1m=fs1.book_imbalance_change_1m,
            spread_change_1m=fs1.spread_change_1m,
            depth_change_1m=fs1.depth_change_1m,
            bid_slope=fs1.bid_slope,
            ask_slope=fs1.ask_slope,
            queue_imbalance_l1=fs1.queue_imbalance_l1,
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
            "book_imbalance_l10": fs.book_imbalance_l10,
            "top_depth_usd": fs.top_depth_usd,
            "top_depth_usd_l10": fs.top_depth_usd_l10,
            "microprice": fs.microprice,
            "liquidity_score": fs.liquidity_score,
            "trend_15m": fs.trend_15m,
            "trend_1h": fs.trend_1h,
            "trend_agree": fs.trend_agree,
            "rsi_14_1m": fs.rsi_14_1m,
            "vol_15m_p90": fs.vol_15m_p90,
            "vol_spike": fs.vol_spike,
            "top_depth_usd_p10": fs.top_depth_usd_p10,
            "liquidity_drop": fs.liquidity_drop,
            "trade_volume_1m": fs.trade_volume_1m,
            "trade_volume_buy_ratio": fs.trade_volume_buy_ratio,
            "trade_count_1m": fs.trade_count_1m,
            "aggr_buy_volume_1m": fs.aggr_buy_volume_1m,
            "aggr_sell_volume_1m": fs.aggr_sell_volume_1m,
            "aggr_delta_1m": fs.aggr_delta_1m,
            "aggr_delta_5m": fs.aggr_delta_5m,
            "volume_imbalance_1m": fs.volume_imbalance_1m,
            "volume_imbalance_5m": fs.volume_imbalance_5m,
            "buy_pressure_1m": fs.buy_pressure_1m,
            "sell_pressure_1m": fs.sell_pressure_1m,
            "absorption_ratio_bid": fs.absorption_ratio_bid,
            "absorption_ratio_ask": fs.absorption_ratio_ask,
            "microprice_change_1m": fs.microprice_change_1m,
            "book_imbalance_change_1m": fs.book_imbalance_change_1m,
            "spread_change_1m": fs.spread_change_1m,
            "depth_change_1m": fs.depth_change_1m,
            "bid_slope": fs.bid_slope,
            "ask_slope": fs.ask_slope,
            "queue_imbalance_l1": fs.queue_imbalance_l1,
            "btc_ret_1m": fs.btc_ret_1m,
            "btc_ret_15m": fs.btc_ret_15m,
            "btc_vol_15m": fs.btc_vol_15m,
            "eth_ret_1m": fs.eth_ret_1m,
            "eth_ret_15m": fs.eth_ret_15m,
            "market_ret_mean_1m": fs.market_ret_mean_1m,
            "market_ret_std_1m": fs.market_ret_std_1m,
            "corr_btc_1h": fs.corr_btc_1h,
            "corr_eth_1h": fs.corr_eth_1h,
            "trend_strength_15m": fs.trend_strength_15m,
            "vol_regime": fs.vol_regime,
            "liq_regime": fs.liq_regime,
        },
        "execution_feedback_15m": {
            "reject_rate_by_symbol": fs.reject_rate_15m,
            "p95_latency_ms_by_symbol": fs.p95_latency_ms_15m,
            "slippage_bps_by_symbol": fs.slippage_bps_15m,
            "reject_rate_delta_by_symbol": fs.reject_rate_15m_delta,
            "p95_latency_ms_delta_by_symbol": fs.p95_latency_ms_15m_delta,
            "slippage_bps_delta_by_symbol": fs.slippage_bps_15m_delta,
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


def _is_15m_boundary(asof_minute: str) -> bool:
    """Return True when asof_minute falls on a 15-minute boundary (minute 0/15/30/45)."""
    s = asof_minute.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.minute % 15 == 0


def maybe_llm_candidate_weights_online(
    fs: FeatureSnapshot15m,
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
) -> Tuple[Optional[Dict[str, float]], Optional[float], Optional[str], Optional[float], Optional[str], Optional[str], Dict[str, Any], Dict[str, Any]]:
    llm_meta: Dict[str, Any] = {"provider": "none"}
    evidence: Dict[str, Any] = {}
    if not AI_USE_LLM:
        return None, None, None, None, None, None, llm_meta, evidence

    # Only call LLM on 15-minute boundaries to match the trading horizon
    if not _is_15m_boundary(fs.asof_minute):
        llm_meta["provider"] = "skipped_non_15m"
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
        LLM_CALLS.labels(SERVICE, "online").inc()
        latency_ms = llm_meta.get("latency_ms") if isinstance(llm_meta, dict) else None
        if isinstance(latency_ms, (int, float)):
            LLM_LAT_MS.labels(SERVICE, "online").observe(float(latency_ms))
        if call_err:
            LLM_ERRORS.labels(SERVICE, "call_error").inc()
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
        LLM_ERRORS.labels(SERVICE, "parse_error").inc()
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
                    AI_FALLBACK.labels(SERVICE, "llm_strict_json_failure").inc()
                else:
                    rationale = "baseline 15m mom/vol + confidence gating + EWMA smoothing + turnover cap"

                # Dynamic caps: aggressive when confidence is high
                if confidence >= AI_CONFIDENCE_HIGH_THRESHOLD:
                    eff_max_gross = MAX_GROSS_HIGH
                    eff_max_net = MAX_NET_HIGH
                    eff_turnover_cap = AI_TURNOVER_CAP_HIGH
                    eff_smooth_alpha = AI_SMOOTH_ALPHA_HIGH
                    confidence_mode = "high"
                else:
                    eff_max_gross = MAX_GROSS
                    eff_max_net = MAX_NET
                    eff_turnover_cap = AI_TURNOVER_CAP
                    eff_smooth_alpha = AI_SMOOTH_ALPHA
                    confidence_mode = "normal"

                capped_symbol_w = apply_symbol_caps(target_w, UNIVERSE, CAP_BTC_ETH, CAP_ALT)
                cash_adjusted_w, cash_weight_in, cash_gross_target = apply_cash_weight(capped_symbol_w, llm_cash_weight, eff_max_gross)
                gross_capped_w = scale_gross(cash_adjusted_w, eff_max_gross)
                gated_w = apply_confidence_gating(gross_capped_w, confidence, AI_MIN_CONFIDENCE)
                smooth_w = ewma_smooth(gated_w, prev_w, eff_smooth_alpha, UNIVERSE)
                capped_w, turnover_before, turnover_after = apply_turnover_cap(smooth_w, current_w, eff_turnover_cap, UNIVERSE)
                candidate_w, net_before, net_after = apply_net_cap(capped_w, eff_max_net)

                rebalance, signal_delta, action_reason = should_rebalance(bus, prev_w, candidate_w, fs.asof_minute)
                final_w = candidate_w if rebalance else prev_w
                decision_action = "REBALANCE" if rebalance else "HOLD"

                gross = sum(abs(v) for v in final_w.values())
                net = sum(final_w.values())
                AI_CONFIDENCE.labels(SERVICE).set(confidence)
                AI_GROSS.labels(SERVICE).set(gross)
                AI_NET.labels(SERVICE).set(net)
                AI_TURNOVER.labels(SERVICE).set(turnover_after)
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
                        "confidence_mode": confidence_mode,
                        "eff_max_gross": eff_max_gross,
                        "eff_max_net": eff_max_net,
                        "eff_turnover_cap": eff_turnover_cap,
                        "eff_smooth_alpha": eff_smooth_alpha,
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
                                "reject_rate_15m_delta": fs.reject_rate_15m_delta,
                                "p95_latency_ms_15m_delta": fs.p95_latency_ms_15m_delta,
                                "slippage_bps_15m_delta": fs.slippage_bps_15m_delta,
                                "trend_15m": fs.trend_15m,
                                "trend_1h": fs.trend_1h,
                                "trend_agree": fs.trend_agree,
                                "rsi_14_1m": fs.rsi_14_1m,
                                "vol_spike": fs.vol_spike,
                                "liquidity_drop": fs.liquidity_drop,
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
