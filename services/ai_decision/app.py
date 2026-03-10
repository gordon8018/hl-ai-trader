import os
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional, Any
from collections import deque

from pydantic import ValidationError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(module)s:%(lineno)d %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ'
)
logger = logging.getLogger(__name__)

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

# ==================== 风险参数（V7）====================
# 基础敞口
MAX_GROSS = float(os.environ.get("MAX_GROSS", "0.50"))                 # 正常模式总敞口上限（50%）
MAX_NET = float(os.environ.get("MAX_NET", "0.30"))                     # 正常模式净敞口上限（30%）
CAP_BTC_ETH = float(os.environ.get("CAP_BTC_ETH", "0.50"))             # BTC/ETH 单品种上限（50%）
CAP_ALT = float(os.environ.get("CAP_ALT", "0.50"))                     # 其他币种单品种上限（50%）

# 置信度与平滑
AI_SMOOTH_ALPHA = float(os.environ.get("AI_SMOOTH_ALPHA", "0.50"))     # 平滑系数（新权重占50%）
AI_SMOOTH_ALPHA_HIGH = float(os.environ.get("AI_SMOOTH_ALPHA_HIGH", "0.70"))  # 高置信度平滑系数
AI_MIN_CONFIDENCE = float(os.environ.get("AI_MIN_CONFIDENCE", "0.40")) # 最低置信度门槛（低于此值会压缩权重）

# 高置信度模式参数
AI_CONFIDENCE_HIGH_THRESHOLD = float(os.environ.get("AI_CONFIDENCE_HIGH_THRESHOLD", "0.70"))
MAX_GROSS_HIGH = float(os.environ.get("MAX_GROSS_HIGH", "0.65"))       # 高置信度总敞口上限（65%）
MAX_NET_HIGH = float(os.environ.get("MAX_NET_HIGH", "0.50"))           # 高置信度净敞口上限（50%）

# 调仓限制
AI_TURNOVER_CAP = float(os.environ.get("AI_TURNOVER_CAP", "0.10"))     # 正常模式单次最大换手率（10%）
AI_TURNOVER_CAP_HIGH = float(os.environ.get("AI_TURNOVER_CAP_HIGH", "0.40")) # 高置信度换手率上限（40%）
AI_SIGNAL_DELTA_THRESHOLD = float(os.environ.get("AI_SIGNAL_DELTA_THRESHOLD", "0.10"))  # 触发调仓的最小信号变化
AI_MIN_MAJOR_INTERVAL_MIN = int(os.environ.get("AI_MIN_MAJOR_INTERVAL_MIN", "15"))      # 主要调仓最小间隔（分钟）

# 方向反转惩罚
DIRECTION_REVERSAL_WINDOW_MIN = int(os.environ.get("DIRECTION_REVERSAL_WINDOW_MIN", "15"))   # 统计窗口（分钟）
DIRECTION_REVERSAL_THRESHOLD = int(os.environ.get("DIRECTION_REVERSAL_THRESHOLD", "2"))       # 触发惩罚的反转次数
DIRECTION_REVERSAL_PENALTY = os.environ.get("DIRECTION_REVERSAL_PENALTY", "scale").lower()   # 惩罚方式：scale（缩放0.5）或 zero（归零）
COOLDOWN_MINUTES = int(os.environ.get("COOLDOWN_MINUTES", "5"))                               # 惩罚后冷静期（分钟）

# 盈亏风控
RECENT_PNL_WINDOW = int(os.environ.get("RECENT_PNL_WINDOW", "5"))         # 记录最近几笔盈亏
MAX_CONSECUTIVE_LOSS = int(os.environ.get("MAX_CONSECUTIVE_LOSS", "3"))   # 连续亏损达到此数则禁用
PNL_DISABLE_DURATION_MIN = int(os.environ.get("PNL_DISABLE_DURATION_MIN", "15"))  # 禁用时长（分钟）
RECENT_LOSS_SCALE_FACTOR = float(os.environ.get("RECENT_LOSS_SCALE_FACTOR", "0.5"))   # 累计亏损超阈值时的缩放因子
RECENT_LOSS_THRESHOLD = float(os.environ.get("RECENT_LOSS_THRESHOLD", "5.0"))         # 累计亏损阈值（美元）

# 动态仓位缩放
SCALE_BY_RECENT_LOSS = os.environ.get("SCALE_BY_RECENT_LOSS", "true").lower() == "true"

# 多空平衡
FORCE_NET_DIRECTION = os.environ.get("FORCE_NET_DIRECTION", "true").lower() == "true"
MAX_NET_LONG_WHEN_DOWN = float(os.environ.get("MAX_NET_LONG_WHEN_DOWN", "0.0"))   # 下跌趋势中允许的最大净多头
MAX_NET_SHORT_WHEN_UP = float(os.environ.get("MAX_NET_SHORT_WHEN_UP", "0.0"))     # 上涨趋势中允许的最大净空头

# 紧急减仓
MAX_SLIPPAGE_EMERGENCY = float(os.environ.get("MAX_SLIPPAGE_EMERGENCY", "5"))         # 触发紧急减仓的滑点阈值（bps）
PRICE_DROP_EMERGENCY_PCT = float(os.environ.get("PRICE_DROP_EMERGENCY_PCT", "2.5"))   # 触发紧急减仓的价格跌幅（%）
FORCE_CASH_WHEN_EXTREME = os.environ.get("FORCE_CASH_WHEN_EXTREME", "true").lower() == "true"

# 基础风险阈值
VOL_REGIME_DEFENSIVE = int(os.environ.get("VOL_REGIME_DEFENSIVE", "1"))        # 波动率防御阈值（≥此值进入防御）
TREND_AGREE_DEFENSIVE = os.environ.get("TREND_AGREE_DEFENSIVE", "true").lower() == "true"
EXEC_DEFENSIVE_REJECT = float(os.environ.get("EXEC_DEFENSIVE_REJECT", "0.05"))
EXEC_DEFENSIVE_LATENCY = float(os.environ.get("EXEC_DEFENSIVE_LATENCY", "500"))
EXEC_DEFENSIVE_SLIPPAGE = float(os.environ.get("EXEC_DEFENSIVE_SLIPPAGE", "8"))

# 其他
AI_DECISION_HORIZON = os.environ.get("AI_DECISION_HORIZON", "15m")
AI_USE_LLM = os.environ.get("AI_USE_LLM", "false").lower() == "true"
AI_LLM_MOCK_RESPONSE = os.environ.get("AI_LLM_MOCK_RESPONSE", "")
AI_LLM_ENDPOINT = os.environ.get("AI_LLM_ENDPOINT", "").strip()
AI_LLM_API_KEY = os.environ.get("AI_LLM_API_KEY", "").strip()
AI_LLM_MODEL = os.environ.get("AI_LLM_MODEL", "").strip()
AI_LLM_TIMEOUT_MS = int(os.environ.get("AI_LLM_TIMEOUT_MS", "1500"))

SERVICE = "ai_decision"
os.environ["SERVICE_NAME"] = SERVICE

# ==================== 更新后的提示词 ====================
SYSTEM_PROMPT = (
    "You are a portfolio construction engine for crypto perpetual futures.\n"
    "ROLE: Convert structured market features into stable, risk-controlled portfolio allocations.\n"
    "OUTPUT: STRICT JSON only. Zero prose outside the JSON object. No markdown.\n\n"
    "=== OUTPUT SCHEMA ===\n"
    "{\n"
    "  \"targets\": [{\"symbol\": str, \"weight\": float}],\n"
    "  \"cash_weight\": float,\n"
    "  \"confidence\": float,\n"
    "  \"rationale\": str,\n"
    "  \"evidence\": {\"regime\": str, \"key_signals\": [str], \"risk_flags\": [str]}\n"
    "}\n\n"
    "=== PORTFOLIO CONSTRAINTS ===\n"
    "- Sum(|targets|) + cash_weight = 1\n"
    "- Positive weight = long, negative = short\n"
    "- cash_weight=1.0 means full cash (maximum defensive)\n"
    "- HIGH CONFIDENCE mode (confidence >= 0.70): max_gross up to 0.80, aggressive sizing allowed, but only if trend strength > 1.0 and trend_agree=1.\n"
    "- NORMAL mode (confidence < 0.70): max_gross <= 0.50, conservative sizing, prefer small adjustments (low turnover).\n"
    "- Single symbol caps: BTC/ETH ≤ 0.50, alts ≤ 0.50. These are absolute maximums; actual weights should reflect conviction (typically 0.10–0.20 in normal conditions).\n\n"
    "=== SIZING GUIDANCE ===\n"
    "- When signals are strong (e.g., trend_strength_15m > 1.5, multiple confirmations), you may allocate up to 0.15–0.20 per symbol.\n"
    "- Aim for total gross exposure between 0.30 and 0.50 in normal conditions, but never exceed 0.50.\n"
    "- Keep cash_weight between 0.10 and 0.30 in trending markets; increase to 0.50+ in defensive regimes.\n\n"
    "=== DECISION FRAMEWORK ===\n"
    "Step 0 - IDENTIFY MARKET REGIME:\n"
    "  - Defensive regime: vol_regime >= 1 OR liq_regime == 0 OR vol_spike == 1 OR liquidity_drop == 1 OR execution problems (reject_rate_avg > 0.05 or slippage_bps_avg > 5).\n"
    "  - Trending regime: trend_agree == 1 AND trend_strength_15m > 1.0 AND vol_regime <= 0 AND liq_regime >= 1.\n"
    "  - Neutral/Conflicting regime: all other cases.\n"
    "  - In defensive regime: cash_weight must be >= 0.7, confidence <= 0.4, no directional exposure > 0.2 per symbol.\n"
    "  - In trending regime: you may allocate up to 80% gross if confidence is high, but only in the direction of the trend.\n"
    "  - In neutral regime: prefer cash_weight >= 0.5, keep individual weights small (< 0.15) and net near zero.\n\n"
    "Step 1 - DIRECTIONAL BIAS:\n"
    "  - Use 15m trend (ret_15m, trend_15m, trend_strength_15m) for primary direction. Confirm with 1h trend (trend_1h).\n"
    "  - If ret_15m and ret_1h have same sign, directional conviction increases.\n"
    "  - If trend_agree == 0 (conflicting timeframes), do NOT take directional risk; set cash_weight >= 0.6.\n"
    "  - **IMPORTANT: In a clear downtrend (ret_15m consistently negative, trend_agree=1), you MUST consider short positions or cash, and NEVER go long.**\n"
    "  - Use RSI (rsi_14_1m) to avoid overbought/oversold entries: if >70 and long, reduce size; if <30 and short, reduce size.\n\n"
    "Step 2 - SIGNAL CONFIRMATION:\n"
    "  - Require at least two of the following signals to agree with directional bias before taking a position > 0.10:\n"
    "      * funding_rate consistent with trend (caution: high funding may indicate crowded trade)\n"
    "      * basis_bps (>50 contango supports longs, < -50 backwardation supports shorts)\n"
    "      * oi_change_15m (>0.05 confirms trend, < -0.05 warns reversal)\n"
    "      * book_imbalance_l1 (|value|>0.3) aligned with direction\n"
    "      * aggr_delta_5m (>0 for uptrend, <0 for downtrend)\n"
    "  - Conflicting signals: reduce gross and increase cash.\n\n"
    "Step 3 - SIZING & TURNOVER CONTROL:\n"
    "  - Prefer small adjustments from previous portfolio (current_weights) to minimize turnover.\n"
    "  - The total absolute change (turnover) should be kept as low as possible. Aim for turnover < 0.05 per 15m decision unless high confidence warrants up to 0.40.\n"
    "  - Never change a position by more than 0.10 in absolute weight unless a clear regime shift occurs.\n"
    "  - Single symbol caps are 0.50 (BTC/ETH) and 0.50 (alts). In normal mode, typical allocations are 0.10–0.20; in high confidence, you may approach the caps.\n"
    "  - In normal mode, distribute weight evenly among similarly strong signals to avoid concentration.\n\n"
    "Step 4 - EXECUTION FEEDBACK ADJUSTMENT:\n"
    "  - If reject_rate_avg > 0.05, p95_latency_ms_avg > 500, or slippage_bps_avg > 5, reduce all weights by 50% and increase cash_weight accordingly.\n"
    "  - If any of these metrics is worsening (delta positive), be extra cautious: cut exposure further.\n"
    "  - For symbols with execution problems (reject_rate_15m > 0.05, slippage_bps_15m > 10), set their weight to zero.\n\n"
    "Step 5 - CALIBRATE CONFIDENCE:\n"
    "  - confidence = 0.0 – 1.0\n"
    "  - >= 0.70: High conviction — all conditions for trending regime met, trend_strength_15m > 1.5, multiple confirming signals, clean execution.\n"
    "  - 0.50 – 0.69: Moderate — trending but with some conflicting signals or moderate volatility.\n"
    "  - < 0.50: Low — defensive regime, conflicting timeframes, or execution issues. In this range, keep cash_weight ≥ 0.6 and any positions ≤ 0.10.\n"
    "  - **If the last 5 trades of a symbol show 3 or more losses, automatically lower confidence to ≤0.3 for that symbol.**\n"
    "  - Be honest: high confidence triggers higher turnover and larger positions, so only use when extremely confident.\n\n"
    "Step 6 - DIRECTION REVERSAL PENALTY:\n"
    "  - If a symbol flips direction (long↔short) more than twice in the last 15 minutes, reduce its weight by 50% for the next 5 minutes.\n\n"
    "Step 7 - EMERGENCY SHUTDOWN:\n"
    "  - If average slippage exceeds 5bps and vol_spike == 1, or any symbol drops more than 2.5% in 2 minutes, set all weights to zero (cash=1).\n\n"
    "=== FEATURE INTERPRETATION (REFERENCE) ===\n"
    "(Keep the detailed feature explanations as in the original prompt, but note the key points above.)\n\n"
    "=== ANTI-HALLUCINATION ===\n"
    "- Only use data provided in the user message. If a field is null/zero/missing, treat as neutral.\n"
    "- Do NOT invent price levels, news, or external events.\n"
    "- If uncertain: raise cash_weight, lower confidence, and keep turnover minimal.\n"
    "- Never violate the output schema or portfolio constraints.\n"
)

# ==================== 辅助函数（保持不变）====================
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
    reversal_counts: Dict[str, int],
    pnl_stats: Dict[str, Any],  # 新增：盈亏统计数据
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
            "direction_reversal_counts": reversal_counts,
            "recent_pnl": pnl_stats,   # 传递给LLM
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
    s = asof_minute.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.minute % 15 == 0

def maybe_llm_candidate_weights_online(
    fs: FeatureSnapshot15m,
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
    reversal_counts: Dict[str, int],
    pnl_stats: Dict[str, Any],
) -> Tuple[Optional[Dict[str, float]], Optional[float], Optional[str], Optional[float], Optional[str], Optional[str], Dict[str, Any], Dict[str, Any]]:
    llm_meta: Dict[str, Any] = {"provider": "none"}
    evidence: Dict[str, Any] = {}
    if not AI_USE_LLM:
        return None, None, None, None, None, None, llm_meta, evidence

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
        user_payload = build_user_payload(fs, current_w, prev_w, reversal_counts, pnl_stats)
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

def adjust_confidence_by_risk(confidence: float, fs: FeatureSnapshot15m) -> float:
    adj = confidence
    reject_avg = sum(fs.reject_rate_15m.values()) / max(len(UNIVERSE), 1)
    slippage_avg = sum(fs.slippage_bps_15m.values()) / max(len(UNIVERSE), 1)
    vol_regime_defensive = fs.vol_regime and any(v >= VOL_REGIME_DEFENSIVE for v in fs.vol_regime.values())
    if vol_regime_defensive or \
       (fs.liq_regime == 0) or \
       (fs.vol_spike == 1) or \
       (fs.liquidity_drop == 1) or \
       (reject_avg > EXEC_DEFENSIVE_REJECT) or \
       (slippage_avg > EXEC_DEFENSIVE_SLIPPAGE):
        adj = min(adj, 0.4)
    if fs.trend_agree == 0 and TREND_AGREE_DEFENSIVE:
        adj = min(adj, 0.5)
    return adj

# ==================== 新增风控函数 ====================

# 记录盈亏
def record_pnl(bus: RedisStreams, symbol: str, pnl: float, timestamp: float) -> None:
    key = f"pnl_history:{symbol}"
    # Redis ZADD 格式：{member: score}，用 pnl 字符串作 member，时间戳作 score
    bus.zadd(key, {str(pnl): timestamp})
    # 限制队列长度
    bus.zremrangebyrank(key, 0, -(RECENT_PNL_WINDOW+1))
    bus.expire(key, 86400)  # 保留一天

def get_recent_pnl(bus: RedisStreams, symbol: str, limit: int) -> List[float]:
    key = f"pnl_history:{symbol}"
    # 获取最近 limit 条，按 score 降序（时间从新到旧）
    items = bus.zrevrange(key, 0, limit-1, withscores=False)
    return [float(x) for x in items]

def check_pnl_penalty(bus: RedisStreams, symbol: str) -> bool:
    """如果最近 RECENT_PNL_WINDOW 笔交易中连续亏损达到 MAX_CONSECUTIVE_LOSS，返回 True（应禁用）"""
    pnls = get_recent_pnl(bus, symbol, RECENT_PNL_WINDOW)
    if len(pnls) < MAX_CONSECUTIVE_LOSS:
        return False
    # 检查最后几笔是否都是亏损
    last_losses = 0
    for p in pnls[:MAX_CONSECUTIVE_LOSS]:  # 最近的是第一个
        if p < 0:
            last_losses += 1
        else:
            break
    return last_losses >= MAX_CONSECUTIVE_LOSS

def get_cumulative_loss(bus: RedisStreams, symbol: str, lookback: int) -> float:
    """返回最近 lookback 笔交易的累计亏损（负数之和）"""
    pnls = get_recent_pnl(bus, symbol, lookback)
    return sum(p for p in pnls if p < 0)

def apply_pnl_penalty(weights: Dict[str, float], bus: RedisStreams) -> Dict[str, float]:
    """根据盈亏风控禁用或缩放权重"""
    new_weights = dict(weights)
    for sym in UNIVERSE:
        # 检查是否应禁用
        disabled_key = f"pnl_disabled:{sym}"
        if bus.exists(disabled_key):
            new_weights[sym] = 0.0
            continue
        if check_pnl_penalty(bus, sym):
            # 禁用该币种
            bus.setex(disabled_key, PNL_DISABLE_DURATION_MIN * 60, "1")
            new_weights[sym] = 0.0
            continue
        # 动态缩放
        if SCALE_BY_RECENT_LOSS:
            cum_loss = get_cumulative_loss(bus, sym, RECENT_PNL_WINDOW)
            if cum_loss <= -RECENT_LOSS_THRESHOLD:
                new_weights[sym] *= RECENT_LOSS_SCALE_FACTOR
    return new_weights

# 多空平衡
def apply_net_direction_cap(weights: Dict[str, float], fs: FeatureSnapshot15m) -> Dict[str, float]:
    """根据市场整体方向限制净敞口"""
    if not FORCE_NET_DIRECTION:
        return weights
    # 使用市场平均回报判断方向 (market_ret_mean_1m 是 Dict[str, float]，取平均值)
    market_ret_dict = fs.market_ret_mean_1m
    if not market_ret_dict:
        return weights
    market_ret = sum(market_ret_dict.values()) / len(market_ret_dict)
    net = sum(weights.values())
    if market_ret < 0 and net > MAX_NET_LONG_WHEN_DOWN:
        # 下跌趋势中净多头超限，等比例缩减多头
        scale = MAX_NET_LONG_WHEN_DOWN / net if net > 0 else 1.0
        new_weights = {}
        for sym, w in weights.items():
            if w > 0:
                new_weights[sym] = w * scale
            else:
                new_weights[sym] = w
        return new_weights
    elif market_ret > 0 and net < -MAX_NET_SHORT_WHEN_UP:
        # 上涨趋势中净空头超限，等比例缩减空头
        scale = (-MAX_NET_SHORT_WHEN_UP) / net if net < 0 else 1.0  # net为负
        new_weights = {}
        for sym, w in weights.items():
            if w < 0:
                new_weights[sym] = w * scale
            else:
                new_weights[sym] = w
        return new_weights
    return weights

# 方向反转惩罚及冷静期
def get_reversal_counts(bus: RedisStreams, symbol: str, window_min: int, now: datetime) -> int:
    key = f"direction_reversals:{symbol}"
    min_score = (now - timedelta(minutes=window_min)).timestamp()
    count = bus.zcount(key, min_score, now.timestamp())
    return int(count)

def record_direction_reversal(bus: RedisStreams, symbol: str, timestamp: float) -> None:
    key = f"direction_reversals:{symbol}"
    bus.zadd(key, {str(timestamp): timestamp})
    bus.expire(key, DIRECTION_REVERSAL_WINDOW_MIN * 60 + 60)

def apply_reversal_penalty(weights: Dict[str, float], prev_w: Dict[str, float], bus: RedisStreams, asof_dt: datetime) -> Dict[str, float]:
    now = asof_dt
    new_weights = dict(weights)
    for sym in UNIVERSE:
        # 检查冷静期
        cooldown_key = f"cooldown:{sym}"
        if bus.exists(cooldown_key):
            new_weights[sym] = 0.0
            continue
        new_w = weights.get(sym, 0.0)
        old_w = prev_w.get(sym, 0.0)
        old_sign = 1 if old_w > 1e-9 else (-1 if old_w < -1e-9 else 0)
        new_sign = 1 if new_w > 1e-9 else (-1 if new_w < -1e-9 else 0)
        if old_sign != 0 and new_sign != 0 and old_sign != new_sign:
            record_direction_reversal(bus, sym, now.timestamp())
        cnt = get_reversal_counts(bus, sym, DIRECTION_REVERSAL_WINDOW_MIN, now)
        if cnt >= DIRECTION_REVERSAL_THRESHOLD:
            # 触发惩罚，设置冷静期
            bus.setex(cooldown_key, COOLDOWN_MINUTES * 60, "1")
            if DIRECTION_REVERSAL_PENALTY == "zero":
                new_weights[sym] = 0.0
            else:
                new_weights[sym] *= 0.5
    return new_weights

# 紧急减仓（增强版）
def check_emergency_shutdown(fs: FeatureSnapshot15m, prev_mid_px: Optional[Dict[str, float]] = None) -> bool:
    slippage_avg = sum(fs.slippage_bps_15m.values()) / max(len(UNIVERSE), 1)
    if slippage_avg > MAX_SLIPPAGE_EMERGENCY and fs.vol_spike == 1:
        return True
    # 检查价格快速下跌
    if prev_mid_px:
        for sym in UNIVERSE:
            prev = prev_mid_px.get(sym)
            curr = fs.mid_px.get(sym)
            if prev and curr and prev > 0:
                drop_pct = (prev - curr) / prev * 100
                if drop_pct > PRICE_DROP_EMERGENCY_PCT:
                    # 简单起见，任一币种满足就触发紧急
                    return True
    return False

def apply_emergency_shutdown(weights: Dict[str, float]) -> Dict[str, float]:
    return {sym: 0.0 for sym in UNIVERSE}

# ==================== 主循环 ====================
def main():
    logger.info(f"Starting ai_decision service | STREAM_IN={STREAM_IN} | CONSUMER={CONSUMER} | AI_SIGNAL_DELTA_THRESHOLD={AI_SIGNAL_DELTA_THRESHOLD}")
    start_metrics("METRICS_PORT", 9103)
    bus = RedisStreams(REDIS_URL)
    error_streak = 0
    alarm_on = False
    # 用于存储上一周期的 mid_px 以检测价格跳水
    last_mid_px: Dict[str, float] = {}
    logger.info("ai_decision service initialized successfully")

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

    logger.info("Entering main processing loop")
    msg_count = 0
    while True:
        msgs = bus.xreadgroup_json(STREAM_IN, GROUP, CONSUMER, count=10, block_ms=5000)
        if not msgs:
            logger.debug("No new messages, waiting...")
        for stream, msg_id, payload in msgs:
            msg_count += 1
            MSG_IN.labels(SERVICE, stream).inc()
            t0 = time.time()
            logger.info(f"Processing message #{msg_count} | stream={stream} | msg_id={msg_id}")
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
                signal_delta_avg = sum(raw_signal.values()) / len(raw_signal) if raw_signal else 0
                logger.info(f"Signal calculation | avg_signal={signal_delta_avg:.4f} | threshold={AI_SIGNAL_DELTA_THRESHOLD} | will_rebalance={signal_delta_avg >= AI_SIGNAL_DELTA_THRESHOLD}")
                used = "baseline_stabilized"
                raw_llm = None
                llm_meta: Dict[str, Any] = {}
                llm_evidence: Dict[str, Any] = {}

                prev_w, _ = get_prev_target(bus, UNIVERSE)
                current_w = get_current_weights(bus, UNIVERSE)
                logger.info(f"Portfolio state | prev_weights={prev_w} | current_weights={current_w}")

                asof_dt = datetime.fromisoformat(fs.asof_minute.replace("Z", "+00:00"))
                reversal_counts = {}
                for sym in UNIVERSE:
                    reversal_counts[sym] = get_reversal_counts(bus, sym, DIRECTION_REVERSAL_WINDOW_MIN, asof_dt)

                # 准备盈亏统计数据（用于传递给LLM）
                pnl_stats = {}
                for sym in UNIVERSE:
                    pnls = get_recent_pnl(bus, sym, RECENT_PNL_WINDOW)
                    pnl_stats[sym] = {
                        "recent": pnls,
                        "consecutive_loss": check_pnl_penalty(bus, sym),
                        "cumulative_loss": get_cumulative_loss(bus, sym, RECENT_PNL_WINDOW),
                    }

                target_w = baseline_w
                llm_parse_error = None
                llm_w, llm_conf, llm_rationale, llm_cash_weight, llm_raw, llm_parse_error, llm_meta, llm_evidence = maybe_llm_candidate_weights_online(
                    fs, current_w, prev_w, reversal_counts, pnl_stats
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

                # 风险调整
                adjusted_confidence = adjust_confidence_by_risk(confidence, fs)

                # 紧急减仓检查（使用上一周期价格）
                emergency = check_emergency_shutdown(fs, last_mid_px)
                if emergency and FORCE_CASH_WHEN_EXTREME:
                    target_w = apply_emergency_shutdown(target_w)
                    used = "emergency_shutdown"
                    rationale = "emergency shutdown due to extreme slippage or rapid price drop"
                    adjusted_confidence = min(adjusted_confidence, 0.2)

                # 更新上一周期价格
                last_mid_px = fs.mid_px.copy()

                # 应用方向反转惩罚
                target_w = apply_reversal_penalty(target_w, prev_w, bus, asof_dt)

                # 应用基于盈亏的风控
                target_w = apply_pnl_penalty(target_w, bus)

                # 确定高/正常模式
                if adjusted_confidence >= AI_CONFIDENCE_HIGH_THRESHOLD:
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

                # 应用所有约束
                capped_symbol_w = apply_symbol_caps(target_w, UNIVERSE, CAP_BTC_ETH, CAP_ALT)
                cash_adjusted_w, cash_weight_in, cash_gross_target = apply_cash_weight(capped_symbol_w, llm_cash_weight, eff_max_gross)
                gross_capped_w = scale_gross(cash_adjusted_w, eff_max_gross)
                gated_w = apply_confidence_gating(gross_capped_w, adjusted_confidence, AI_MIN_CONFIDENCE)
                smooth_w = ewma_smooth(gated_w, prev_w, eff_smooth_alpha, UNIVERSE)
                capped_w, turnover_before, turnover_after = apply_turnover_cap(smooth_w, current_w, eff_turnover_cap, UNIVERSE)
                # 多空平衡
                capped_w = apply_net_direction_cap(capped_w, fs)
                candidate_w, net_before, net_after = apply_net_cap(capped_w, eff_max_net)

                rebalance, signal_delta, action_reason = should_rebalance(bus, prev_w, candidate_w, fs.asof_minute)
                final_w = candidate_w if rebalance else prev_w
                decision_action = "REBALANCE" if rebalance else "HOLD"
                logger.info(f"Decision | action={decision_action} | signal_delta={signal_delta:.4f} | reason={action_reason} | adjusted_confidence={adjusted_confidence:.2f}")

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
                        "adjusted_confidence": adjusted_confidence,
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
                    "risk_flags": {
                        "emergency_shutdown": emergency,
                        "direction_reversal_counts": reversal_counts,
                        "pnl_penalty": {sym: check_pnl_penalty(bus, sym) for sym in UNIVERSE},
                    },
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
                    model={"name": used, "version": "v6"},  # 版本升级
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
                stream_id = bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": tp.model_dump()}))
                logger.info(f"Wrote to {STREAM_OUT} | stream_id={stream_id} | decision_action={decision_action} | cash_weight={tp.cash_weight:.2f}")
                bus.set_json(LATEST_ALPHA_KEY, {"env": env.model_dump(), "data": tp.model_dump()}, ex=3600)
                if rebalance:
                    now_dt = datetime.fromisoformat(fs.asof_minute.replace("Z", "+00:00"))
                    bus.set_json(LATEST_MAJOR_TS_KEY, {"ts": now_dt.timestamp(), "cycle_id": major_cycle}, ex=3600)
                    logger.info(f"Major rebalance recorded | cycle_id={major_cycle}")
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
                                "adjusted_confidence": adjusted_confidence,
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
                                "emergency_shutdown": emergency,
                                "direction_reversal_counts": reversal_counts,
                                "pnl_penalty": {sym: check_pnl_penalty(bus, sym) for sym in UNIVERSE},
                            },
                        }
                    ),
                )
                MSG_OUT.labels(SERVICE, STREAM_OUT).inc()
                MSG_OUT.labels(SERVICE, AUDIT).inc()

                bus.xack(STREAM_IN, GROUP, msg_id)
                note_ok(env)
                elapsed = time.time() - t0
                LAT.labels(SERVICE, "cycle").observe(elapsed)
                logger.info(f"Message processed successfully | elapsed={elapsed:.3f}s")
            except Exception as e:
                logger.error(f"Error processing message | error={str(e)}", exc_info=True)
                env = Envelope(source="ai_decision", cycle_id=current_cycle_id())
                retry_or_dlq(bus, STREAM_IN, payload, env.model_dump(), RETRY, reason=str(e))
                bus.xack(STREAM_IN, GROUP, msg_id)
                note_error(env, "handler", e)

if __name__ == "__main__":
    main()