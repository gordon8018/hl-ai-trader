import os
import re
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
    DirectionBias,
    SymbolBias,
    FeatureSnapshot1h,
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
    AI_TARGET_ACTUAL_GAP_GROSS,
    DAILY_TRADE_COUNT,
)

# ==================== 配置加载 ====================
import os as _os
from services.ai_decision.config_loader import load_config as _load_config

_CONFIG_PATH = _os.environ.get("AI_CONFIG_PATH")  # 仅供测试覆盖，生产不设置
_cfg = _load_config(_CONFIG_PATH)

# REDIS_URL 是必填项，缺失时立即报错（与原 os.environ["REDIS_URL"] 行为一致）
if "REDIS_URL" not in _cfg:
    raise KeyError("REDIS_URL must be specified in trading_params.json")


def _get(key, cast=str, default=None):
    """从JSON配置中读取参数。cast为bool时直接返回bool值，不经str转换。"""
    val = _cfg.get(key, default)
    if val is None:
        return default
    if cast is bool:
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"
    return cast(val)


# ── 基础 ──────────────────────────────────────────────────────────────
UNIVERSE                        = _get("UNIVERSE", str, "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD          = _get("ERROR_STREAK_THRESHOLD", int, 3)
STREAM_IN                       = _get("STREAM_IN", str, "md.features.15m")
REDIS_URL                       = _cfg["REDIS_URL"]  # 必填，不使用默认值
CONSUMER                        = _get("CONSUMER", str, "ai_1")
RETRY                           = RetryPolicy(max_retries=_get("MAX_RETRIES", int, 5))
LAYER1_POLL_BLOCK_MS            = max(1, _get("LAYER1_POLL_BLOCK_MS", int, 1))

STREAM_OUT = "alpha.target"
AUDIT = "audit.logs"
STATE_KEY = "latest.state.snapshot"
LATEST_ALPHA_KEY = "latest.alpha.target"
LATEST_MAJOR_TS_KEY = "latest.alpha.major.ts"

GROUP = "ai_grp"

# ── 敞口 ──────────────────────────────────────────────────────────────
MAX_GROSS                       = _get("MAX_GROSS", float, 0.35)
MAX_NET                         = _get("MAX_NET", float, 0.20)
CAP_BTC_ETH                     = _get("CAP_BTC_ETH", float, 0.30)
CAP_ALT                         = _get("CAP_ALT", float, 0.15)

# ── 平滑与置信度 ───────────────────────────────────────────────────────
AI_SMOOTH_ALPHA                 = _get("AI_SMOOTH_ALPHA", float, 0.25)
AI_SMOOTH_ALPHA_HIGH            = _get("AI_SMOOTH_ALPHA_HIGH", float, 0.60)
AI_SMOOTH_ALPHA_MID             = _get("AI_SMOOTH_ALPHA_MID", float, 0.40)
AI_MIN_CONFIDENCE               = _get("AI_MIN_CONFIDENCE", float, 0.50)
AI_CONFIDENCE_HIGH_THRESHOLD    = _get("AI_CONFIDENCE_HIGH_THRESHOLD", float, 0.75)
MAX_GROSS_HIGH                  = _get("MAX_GROSS_HIGH", float, 0.50)
MAX_NET_HIGH                    = _get("MAX_NET_HIGH", float, 0.35)

# ── 换手控制 ──────────────────────────────────────────────────────────
AI_TURNOVER_CAP                 = _get("AI_TURNOVER_CAP", float, 0.05)
AI_TURNOVER_CAP_HIGH            = _get("AI_TURNOVER_CAP_HIGH", float, 0.20)
AI_SIGNAL_DELTA_THRESHOLD       = _get("AI_SIGNAL_DELTA_THRESHOLD", float, 0.15)
AI_MIN_MAJOR_INTERVAL_MIN       = _get("AI_MIN_MAJOR_INTERVAL_MIN", int, 30)
POSITION_TARGET_MISMATCH_EPSILON = _get("POSITION_TARGET_MISMATCH_EPSILON", float, 0.002)

# ── 方向反转 ──────────────────────────────────────────────────────────
DIRECTION_REVERSAL_WINDOW_MIN   = _get("DIRECTION_REVERSAL_WINDOW_MIN", int, 30)
DIRECTION_REVERSAL_THRESHOLD    = _get("DIRECTION_REVERSAL_THRESHOLD", int, 2)
DIRECTION_REVERSAL_PENALTY      = _get("DIRECTION_REVERSAL_PENALTY", str, "zero").lower()
COOLDOWN_MINUTES                = _get("COOLDOWN_MINUTES", int, 15)

# ── PnL 风控 ──────────────────────────────────────────────────────────
RECENT_PNL_WINDOW               = _get("RECENT_PNL_WINDOW", int, 5)
MAX_CONSECUTIVE_LOSS            = _get("MAX_CONSECUTIVE_LOSS", int, 2)
PNL_DISABLE_DURATION_MIN        = _get("PNL_DISABLE_DURATION_MIN", int, 60)
RECENT_LOSS_SCALE_FACTOR        = _get("RECENT_LOSS_SCALE_FACTOR", float, 0.3)
RECENT_LOSS_THRESHOLD           = _get("RECENT_LOSS_THRESHOLD", float, 3.0)

DAILY_DRAWDOWN_HALT_USD         = _get("DAILY_DRAWDOWN_HALT_USD", float, 3.0)
DAILY_DRAWDOWN_RESUME_HOURS     = _get("DAILY_DRAWDOWN_RESUME_HOURS", float, 4.0)
PORTFOLIO_LOSS_COUNTER_SHARED   = _get("PORTFOLIO_LOSS_COUNTER_SHARED", bool, True)
SCALE_BY_RECENT_LOSS            = _get("SCALE_BY_RECENT_LOSS", bool, True)

# ── 方向管控 ──────────────────────────────────────────────────────────
FORCE_NET_DIRECTION             = _get("FORCE_NET_DIRECTION", bool, True)
MAX_NET_LONG_WHEN_DOWN          = _get("MAX_NET_LONG_WHEN_DOWN", float, 0.0)
MAX_NET_SHORT_WHEN_UP           = _get("MAX_NET_SHORT_WHEN_UP", float, 0.0)
BEARISH_REGIME_LONG_BLOCK       = _get("BEARISH_REGIME_LONG_BLOCK", bool, True)
BEARISH_REGIME_RET1H_THRESHOLD  = _get("BEARISH_REGIME_RET1H_THRESHOLD", float, -0.005)

# ── 紧急减仓 ──────────────────────────────────────────────────────────
MAX_SLIPPAGE_EMERGENCY          = _get("MAX_SLIPPAGE_EMERGENCY", float, 5)
PRICE_DROP_EMERGENCY_PCT        = _get("PRICE_DROP_EMERGENCY_PCT", float, 2.0)
FORCE_CASH_WHEN_EXTREME         = _get("FORCE_CASH_WHEN_EXTREME", bool, True)

# ── 执行防御 ──────────────────────────────────────────────────────────
VOL_REGIME_DEFENSIVE            = _get("VOL_REGIME_DEFENSIVE", int, 1)
TREND_AGREE_DEFENSIVE           = _get("TREND_AGREE_DEFENSIVE", bool, True)
EXEC_DEFENSIVE_REJECT           = _get("EXEC_DEFENSIVE_REJECT", float, 0.05)
EXEC_DEFENSIVE_LATENCY          = _get("EXEC_DEFENSIVE_LATENCY", float, 500)
EXEC_DEFENSIVE_SLIPPAGE         = _get("EXEC_DEFENSIVE_SLIPPAGE", float, 8)

# ── 订单整合 ──────────────────────────────────────────────────────────
ORDER_CONSOLIDATE_PER_CYCLE     = _get("ORDER_CONSOLIDATE_PER_CYCLE", bool, True)
MAX_ORDERS_PER_COIN_PER_CYCLE   = _get("MAX_ORDERS_PER_COIN_PER_CYCLE", int, 1)

# ── 持仓管理 ──────────────────────────────────────────────────────────
POSITION_MAX_AGE_MIN            = _get("POSITION_MAX_AGE_MIN", int, 30)
POSITION_PROFIT_TARGET_BPS      = _get("POSITION_PROFIT_TARGET_BPS", float, 15.0)
POSITION_STOP_LOSS_BPS          = _get("POSITION_STOP_LOSS_BPS", float, 40.0)

# ── LLM 配置 ──────────────────────────────────────────────────────────
AI_DECISION_HORIZON             = _get("AI_DECISION_HORIZON", str, "30m")
AI_USE_LLM                      = _get("AI_USE_LLM", bool, False)
AI_LLM_MOCK_RESPONSE            = _get("AI_LLM_MOCK_RESPONSE", str, "")
AI_LLM_ENDPOINT                 = _get("AI_LLM_ENDPOINT", str, "").strip()
AI_LLM_API_KEY                  = _get("AI_LLM_API_KEY", str, "").strip()
AI_LLM_MODEL                    = _get("AI_LLM_MODEL", str, "").strip()
AI_LLM_TIMEOUT_MS               = _get("AI_LLM_TIMEOUT_MS", int, 1500)

SERVICE = "ai_decision"
os.environ["SERVICE_NAME"] = SERVICE

# ── Layer1/2 流配置 ───────────────────────────────────────────────────
STREAM_IN_1H                    = _get("STREAM_IN_1H", str, "md.features.1h")
DIRECTION_BIAS_KEY              = "latest.direction_bias"
GROUP_1H                        = "ai_grp_1h"
CONSUMER_1H                     = _get("CONSUMER_1H", str, "ai_layer1_1")

# ── 资金利用率（mode-aware） ───────────────────────────────────────────
MIN_NOTIONAL_USD                = _get("MIN_NOTIONAL_USD", float, 50.0)
MAX_TRADES_PER_DAY              = _get("MAX_TRADES_PER_DAY", int, 15)
MIN_TRADE_INTERVAL_MIN          = _get("MIN_TRADE_INTERVAL_MIN", int, 90)

MAX_GROSS_TRENDING_HIGH         = _get("MAX_GROSS_TRENDING_HIGH", float, 0.65)
MAX_GROSS_TRENDING_MID          = _get("MAX_GROSS_TRENDING_MID", float, 0.45)
MAX_GROSS_SIDEWAYS              = _get("MAX_GROSS_SIDEWAYS", float, 0.00)
MAX_GROSS_VOLATILE              = _get("MAX_GROSS_VOLATILE", float, 0.20)
MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD = _get("MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD", float, 0.70)

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
    "- Sum(|targets|) + cash_weight = 1.0 (must be exact)\n"
    "- Positive weight = long, negative = short\n"
    "- cash_weight = 1.0 means full cash (maximum defensive)\n"
    "- NORMAL mode (confidence < 0.75): max_gross <= 0.35, max_net <= 0.20\n"
    "- HIGH CONFIDENCE mode (confidence >= 0.75): max_gross <= 0.50, max_net <= 0.35\n"
    "  Requires ALL of: trend_strength_15m > 1.5, trend_agree = 1, vol_regime <= 0, liq_regime >= 1\n"
    "- Single symbol caps: BTC/ETH <= 0.30, alts (ADA/DOGE/SOL/other) <= 0.15\n"
    "- Typical non-trivial allocation: 0.08–0.15 per symbol. Never approach caps unless extremely convicted.\n"
    "- cash_weight minimum: 0.30 in trending markets, 0.70+ in defensive regimes\n\n"

    "=== STEP 0 — IDENTIFY MARKET REGIME (do this first, it gates all other steps) ===\n"
    "Classify the current regime into exactly one of four states:\n\n"

    "  [A] EMERGENCY — immediate shutdown:\n"
    "      Triggers: slippage_bps_avg > 5 AND vol_spike = 1\n"
    "              OR any symbol dropped > 2.0% in last 2 minutes\n"
    "              OR portfolio daily_pnl_usd < -3.0 (cumulative, provided in context)\n"
    "      Action: set ALL weights to zero, cash_weight = 1.0, confidence = 0.0\n"
    "      In rationale: state 'EMERGENCY_SHUTDOWN' and the trigger reason.\n\n"

    "  [B] DEFENSIVE — high caution:\n"
    "      Triggers: vol_regime >= 1 OR liq_regime = 0 OR vol_spike = 1\n"
    "              OR liquidity_drop = 1 OR reject_rate_avg > 0.05\n"
    "              OR slippage_bps_avg > 5 OR p95_latency_ms_avg > 500\n"
    "      Action: cash_weight >= 0.70, confidence <= 0.40\n"
    "              No directional weight > 0.10 per symbol\n"
    "              Reduce existing positions by 50% from current_weights\n\n"

    "  [C] BEARISH — sustained downtrend detected:\n"
    "      Triggers: ret_1h < -0.005 AND trend_agree = 1 AND vol_regime >= 1\n"
    "              OR (ret_15m < 0 AND ret_1h < 0 AND trend_15m < 0 AND trend_1h < 0)\n"
    "      Action: HARD BLOCK on all new long positions (positive weights)\n"
    "              You MUST set all long weights to exactly 0.0\n"
    "              Short positions allowed only if confidence >= 0.60 AND at least 2 confirming signals\n"
    "              cash_weight >= 0.60 unless shorting with high confidence\n"
    "              If portfolio_consecutive_losses >= 2: cash_weight = 1.0, no new shorts either\n"
    "              In risk_flags: include 'BEARISH_REGIME_LONG_BLOCKED'\n\n"

    "  [D] TRENDING — active opportunity:\n"
    "      Triggers: trend_agree = 1 AND trend_strength_15m > 1.0\n"
    "              AND vol_regime <= 0 AND liq_regime >= 1\n"
    "              AND no execution problems\n"
    "      Action: allocate directionally, cash_weight 0.30–0.50\n"
    "              Confirm direction with at least 2 signals before weight > 0.10\n\n"

    "  [E] NEUTRAL — default (all other cases):\n"
    "      Action: cash_weight >= 0.60, individual weights < 0.10, net exposure near zero\n\n"

    "IMPORTANT: State the classified regime as the first word of 'rationale'.\n"
    "If regime is BEARISH or DEFENSIVE and you output any long weight > 0, that is a CRITICAL ERROR.\n\n"

    "=== STEP 1 — DIRECTIONAL BIAS (only after regime is not EMERGENCY) ===\n"
    "Primary direction from 15m trend (ret_15m, trend_15m, trend_strength_15m).\n"
    "Confirm with 1h trend (trend_1h, ret_1h).\n\n"

    "  1a. TREND AGREEMENT CHECK:\n"
    "      If trend_agree = 0 (conflicting timeframes): cash_weight >= 0.65, no weight > 0.08\n"
    "      If ret_15m and ret_1h have OPPOSITE signs: treat as neutral, reduce all weights by 50%\n\n"

    "  1b. BEARISH CONFIRMATION (critical — this was the main failure mode in backtesting):\n"
    "      Count bearish signals: ret_15m < 0, ret_1h < 0, trend_15m < 0, trend_1h < 0, trend_agree = 1\n"
    "      If 4 or more bearish signals present: regime is BEARISH regardless of Step 0 classification\n"
    "      You MUST NOT open long positions when 4+ bearish signals are active.\n"
    "      Ask yourself: 'Would I buy this asset right now knowing it has been falling for 1h+?' If no, don't.\n\n"

    "  1c. RSI FILTER:\n"
    "      rsi_14_1m > 70 and considering long: reduce long weight by 40%\n"
    "      rsi_14_1m < 30 and considering short: reduce short weight by 40%\n\n"

    "=== STEP 2 — SIGNAL CONFIRMATION (require 2+ of these before weight > 0.10) ===\n"
    "  * funding_rate aligned with trend direction (positive for longs, negative for shorts)\n"
    "    Caution: funding_rate > 0.001 with crowded long = contrarian signal, reduce longs\n"
    "  * basis_bps: > 50 supports longs, < -50 supports shorts\n"
    "  * oi_change_15m: > 0.05 confirms trend, < -0.05 warns reversal (exit, not entry)\n"
    "  * book_imbalance_l1: |value| > 0.3, direction aligned\n"
    "  * aggr_delta_5m: > 0 for uptrend confirmation, < 0 for downtrend\n\n"
    "  If fewer than 2 signals confirm: cap weight at 0.08, raise cash by the difference.\n"
    "  Conflicting signals (some bullish, some bearish): cash_weight += 0.15, reduce all weights 30%.\n\n"

    "=== STEP 3 — SIZING & TURNOVER CONTROL ===\n"
    "  Rule 1 — CONSOLIDATION: output exactly ONE weight per symbol. Never split a symbol across\n"
    "           multiple entries in targets[]. The execution layer will place a single order.\n\n"
    "  Rule 2 — MINIMUM CHANGE THRESHOLD: only adjust a symbol's weight if the change from\n"
    "           current_weights exceeds 0.08 (8% of portfolio). Smaller adjustments are noise.\n"
    "           If abs(new_weight - current_weight) < 0.08: keep current_weight unchanged.\n\n"
    "  Rule 3 — MAXIMUM SINGLE-SYMBOL CHANGE: never change any symbol by more than 0.10 per cycle\n"
    "           UNLESS: (a) regime just changed to EMERGENCY/BEARISH, or (b) confidence >= 0.75\n\n"
    "  Rule 4 — TOTAL TURNOVER CAP: sum of all |weight changes| <= 0.05 in normal mode,\n"
    "           <= 0.20 in high confidence mode.\n\n"
    "  Rule 5 — ALTCOIN RESTRAINT: for ADA, DOGE, SOL and other non-BTC/ETH symbols,\n"
    "           require confidence >= 0.65 before any allocation. Default weight = 0 for alts\n"
    "           unless strong confirming signals. Historical performance: alts lost 3× more than BTC.\n\n"

    "=== STEP 4 — POSITION AGE & STALE SIGNAL MANAGEMENT ===\n"
    "  Context will include position_age_min for each open position.\n"
    "  If position_age_min > 30 AND position is not profitable: set that symbol's weight to 0.0\n"
    "  Rationale: the 15m signal that opened this position has expired. Don't hold losers on stale logic.\n"
    "  If position_age_min > 30 AND position is profitable: keep but do not add to it.\n"
    "  Never size-up an existing losing position ('averaging down' is forbidden).\n\n"

    "=== STEP 5 — EXECUTION FEEDBACK ADJUSTMENT ===\n"
    "  If reject_rate_avg > 0.05: reduce ALL weights by 50%, increase cash accordingly\n"
    "  If p95_latency_ms_avg > 500: reduce ALL weights by 30%\n"
    "  If slippage_bps_avg > 5: reduce ALL weights by 50%\n"
    "  If any metric WORSENING (delta positive): apply an additional 20% reduction\n"
    "  Per-symbol: if reject_rate_15m > 0.05 OR slippage_bps_15m > 10: set that symbol's weight = 0\n\n"

    "=== STEP 6 — CALIBRATE CONFIDENCE ===\n"
    "  confidence = 0.0 – 1.0. This value controls turnover and sizing limits at execution.\n"
    "  Be conservative — overestimating confidence in backtesting was a primary cause of losses.\n\n"

    "  >= 0.75: High conviction. Requires ALL of:\n"
    "           trend_agree = 1, trend_strength_15m > 1.5, vol_regime <= 0, liq_regime >= 1,\n"
    "           at least 3 confirming signals, clean execution metrics, no consecutive losses\n\n"

    "  0.55 – 0.74: Moderate. Trending with some noise. At least 2 confirming signals.\n\n"

    "  0.50 – 0.54: Weak positive. Use only to justify a small directional lean (0.05–0.08 weight).\n\n"

    "  < 0.50: LOW. MANDATORY behavior:\n"
    "          cash_weight >= 0.70, no individual weight > 0.08\n"
    "          This includes: defensive regime, conflicting timeframes, execution issues\n\n"

    "  AUTO-DOWNGRADE rules (apply before finalizing confidence):\n"
    "  - If portfolio_consecutive_losses >= 2 (shared counter): confidence = min(confidence, 0.35)\n"
    "  - If last 5 trades for this symbol show >= 3 losses: confidence = min(confidence, 0.30)\n"
    "  - If regime was BEARISH in the last 30 minutes: confidence = min(confidence, 0.45)\n"
    "  - If current weights already show a loss on this symbol: confidence -= 0.10\n\n"

    "=== STEP 7 — DIRECTION REVERSAL PENALTY ===\n"
    "  Track: if a symbol flipped direction (long ↔ short) more than 2 times in the last 30 minutes:\n"
    "  Action: set that symbol's weight to EXACTLY 0.0 for this cycle.\n"
    "  Add 'REVERSAL_PENALTY:{symbol}' to risk_flags.\n"
    "  Do NOT reduce to 50% — set to zero. Chasing direction in volatile conditions destroys PnL.\n\n"

    "=== STEP 8 — FINAL VALIDATION (run before outputting JSON) ===\n"
    "  Check 1: Sum(|targets weights|) + cash_weight == 1.0 ± 0.001. If not, normalize.\n"
    "  Check 2: No symbol weight exceeds its cap (BTC/ETH: 0.30, alts: 0.15).\n"
    "  Check 3: If regime is BEARISH or DEFENSIVE, verify no positive weights exist.\n"
    "  Check 4: If confidence < 0.50, verify cash_weight >= 0.70.\n"
    "  Check 5: Total turnover (sum of |new_weight - current_weight| for all symbols) <= turnover cap.\n"
    "  Check 6: No symbol appears more than once in targets[].\n"
    "  If any check fails: fix the violation, then output. Never output a constraint-violating portfolio.\n\n"

    "=== CONTEXT FIELDS (provided per call, use all of them) ===\n"
    "  daily_pnl_usd: float            # cumulative PnL today in USD (negative = losing day)\n"
    "  portfolio_consecutive_losses: int  # shared loss counter across all symbols\n"
    "  position_age_min: {symbol: int}   # minutes each current position has been open\n"
    "  current_weights: {symbol: float}  # current live weights before this decision\n"
    "  symbol_recent_loss_count: {symbol: int}  # losses in last 5 trades per symbol\n\n"

    "=== ANTI-HALLUCINATION ===\n"
    "- Only use data provided in the user message. Never invent price levels, news, or events.\n"
    "- If a field is null/zero/missing: treat as neutral, do not assume a direction.\n"
    "- If uncertain about regime: classify as NEUTRAL (not TRENDING). Caution earns more than aggression.\n"
    "- The prompt's constraints are non-negotiable. A creative interpretation that violates a constraint\n"
    "  (e.g., 'I'm 90% confident so I'll use 0.60 gross despite the 0.50 cap') is a hard error.\n"
    "- Never violate the output schema. Never add fields not in the schema.\n"
)

SYSTEM_PROMPT_1H = (
    "You are a directional bias engine for crypto perpetual futures. "
    "Determine the likely price direction for the next 1-4 hours per symbol.\n"
    "OUTPUT: STRICT JSON only. Zero prose outside the JSON object.\n\n"

    "=== OUTPUT SCHEMA ===\n"
    "{\n"
    "  \"biases\": [{\"symbol\": str, \"direction\": \"LONG\"|\"SHORT\"|\"FLAT\", \"confidence\": float}],\n"
    "  \"market_state\": \"TRENDING\"|\"SIDEWAYS\"|\"VOLATILE\"|\"EMERGENCY\",\n"
    "  \"rationale\": str (max 300 chars)\n"
    "}\n\n"

    "=== DECISION RULES ===\n"
    "STEP -1 — SIDEWAYS filter (output FLAT if 2+ conditions met for a symbol):\n"
    "  - |ret_1h| < 0.003 AND trend_agree == 0\n"
    "  - vol_regime == 0\n"
    "  - rsi_14_1h between 45-55\n"
    "  - aggr_delta_1h sign differs from trend_1h sign\n"
    "  If market_state is SIDEWAYS for majority of symbols → set market_state=SIDEWAYS\n\n"

    "STEP 1 — TRENDING/VOLATILE regime:\n"
    "  VOLATILE: vol_spike signals or |ret_4h| > 0.03 → reduce all confidence by 0.15, prefer FLAT\n"
    "  TRENDING: trend_1h and trend_4h agree AND |ret_4h| > 0.005\n\n"

    "STEP 2 — Direction for TRENDING symbols (need 3+ signals for LONG, 3+ for SHORT):\n"
    "  LONG signals: ret_1h > 0.003, trend_1h > 0, trend_4h > 0, funding_rate > 0, rsi_14_1h < 65, aggr_delta_1h > 0\n"
    "  SHORT signals: ret_1h < -0.003, trend_1h < 0, trend_4h < 0, funding_rate < 0.0003, rsi_14_1h > 35, aggr_delta_1h < 0\n"
    "  < 3 signals → FLAT\n\n"

    "STEP 3 — Confidence calibration:\n"
    "  3 signals: confidence 0.65\n"
    "  4 signals: confidence 0.72\n"
    "  5+ signals: confidence 0.80\n"
    "  < 3 signals or FLAT: confidence ≤ 0.50\n\n"

    "CONSTRAINTS:\n"
    "  - Minimum confidence to output LONG/SHORT: 0.65\n"
    "  - If confidence < 0.65 → direction = FLAT\n"
    "  - Never output LONG during EMERGENCY market_state\n"
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

def get_position_pnl_bps(bus: RedisStreams, universe: List[str]) -> Dict[str, float]:
    """从 STATE_KEY 读取各 symbol 浮动 PnL（基点）。

    计算公式：(mark_px - entry_px) / entry_px * 10000
    做空头寸 qty < 0 时方向自动修正：pnl_bps = (entry_px - mark_px) / entry_px * 10000

    Returns:
        {symbol: pnl_bps}，无持仓或数据缺失时为 0.0。
    """
    raw = bus.get_json(STATE_KEY)
    if not raw or "data" not in raw:
        return {sym: 0.0 for sym in universe}
    positions = raw.get("data", {}).get("positions", {}) or {}
    out: Dict[str, float] = {sym: 0.0 for sym in universe}
    for sym in universe:
        pos = positions.get(sym)
        if not isinstance(pos, dict):
            continue
        qty = float(pos.get("qty", 0.0) or 0.0)
        entry_px = float(pos.get("entry_px", 0.0) or 0.0)
        mark_px = float(pos.get("mark_px", 0.0) or 0.0)
        if abs(qty) < 1e-12 or entry_px <= 0.0:
            continue
        raw_bps = (mark_px - entry_px) / entry_px * 10_000.0
        # 做空时 qty < 0，PnL 方向取反
        out[sym] = raw_bps if qty > 0 else -raw_bps
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
    """Return the current 1-hour boundary cycle id, e.g. '20260316T1500Z'."""
    s = asof_minute.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    dt = dt.replace(minute=0, second=0, microsecond=0)   # floor to hour
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

def should_rebalance(
    bus: RedisStreams,
    prev_w: Dict[str, float],
    current_w: Dict[str, float],
    candidate_w: Dict[str, float],
    asof_minute: str,
) -> Tuple[bool, float, str]:
    delta = sum(abs(candidate_w.get(sym, 0.0) - current_w.get(sym, 0.0)) for sym in UNIVERSE)
    if delta < AI_SIGNAL_DELTA_THRESHOLD:
        return False, delta, "signal_delta_below_threshold_vs_current"

    raw = bus.get_json(LATEST_MAJOR_TS_KEY)
    if raw and isinstance(raw, dict):
        last_ts = raw.get("ts")
        if isinstance(last_ts, (int, float)):
            now_dt = datetime.fromisoformat(asof_minute.replace("Z", "+00:00"))
            now_ts = now_dt.replace(second=0, microsecond=0).timestamp()
            if (now_ts - float(last_ts)) < (AI_MIN_MAJOR_INTERVAL_MIN * 60):
                return False, delta, "min_major_interval"

    return True, delta, "major_rebalance"


def detect_position_target_mismatch(
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
    epsilon: float = POSITION_TARGET_MISMATCH_EPSILON,
) -> Tuple[bool, float, float]:
    current_gross = sum(abs(v) for v in current_w.values())
    prev_gross = sum(abs(v) for v in prev_w.values())
    mismatch = current_gross > epsilon and prev_gross <= epsilon
    return mismatch, current_gross, prev_gross

def build_user_payload(
    fs: FeatureSnapshot15m,
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
    reversal_counts: Optional[Dict[str, int]] = None,
    pnl_stats: Optional[Dict[str, Any]] = None,
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
            "direction_reversal_counts": reversal_counts or {},
            "recent_pnl": pnl_stats or {},
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
    liq_regime_defensive = fs.liq_regime and any(v <= 0 for v in fs.liq_regime.values())
    vol_spike_on = fs.vol_spike and any(v >= 1 for v in fs.vol_spike.values())
    liquidity_drop_on = fs.liquidity_drop and any(v >= 1 for v in fs.liquidity_drop.values())
    trend_disagree = fs.trend_agree and any(v <= 0 for v in fs.trend_agree.values())
    if vol_regime_defensive or \
       liq_regime_defensive or \
       vol_spike_on or \
       liquidity_drop_on or \
       (reject_avg > EXEC_DEFENSIVE_REJECT) or \
       (slippage_avg > EXEC_DEFENSIVE_SLIPPAGE):
        adj = min(adj, 0.4)
    if trend_disagree and TREND_AGREE_DEFENSIVE:
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
    vol_spike_on = fs.vol_spike and any(v >= 1 for v in fs.vol_spike.values())
    if slippage_avg > MAX_SLIPPAGE_EMERGENCY and vol_spike_on:
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

# ==================== Layer 1 helpers ====================
def parse_direction_bias(raw_text: str, universe: List[str]) -> Optional[DirectionBias]:
    """Parse LLM JSON response into DirectionBias, filling missing symbols as FLAT."""
    try:
        text = raw_text.strip()
        # Extract JSON from possible markdown code blocks
        if "```" in text:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
        data = json.loads(text)
        biases_raw = data.get("biases", [])
        market_state = data.get("market_state", "NEUTRAL")
        if market_state not in ("TRENDING", "SIDEWAYS", "VOLATILE", "EMERGENCY"):
            market_state = "TRENDING"
        rationale = str(data.get("rationale", ""))[:300]

        # Build bias dict, fill missing as FLAT
        bias_map = {}
        for b in biases_raw:
            sym = b.get("symbol")
            if sym in universe:
                direction = b.get("direction", "FLAT")
                if direction not in ("LONG", "SHORT", "FLAT"):
                    direction = "FLAT"
                conf = float(b.get("confidence", 0.5))
                conf = max(0.0, min(1.0, conf))
                bias_map[sym] = SymbolBias(symbol=sym, direction=direction, confidence=conf)

        for sym in universe:
            if sym not in bias_map:
                bias_map[sym] = SymbolBias(symbol=sym, direction="FLAT", confidence=0.0)

        now_dt = datetime.now(timezone.utc)
        valid_until = (now_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00Z")

        return DirectionBias(
            asof_minute=now_dt.strftime("%Y-%m-%dT%H:%M:00Z"),
            valid_until_minute=valid_until,
            market_state=market_state,
            biases=list(bias_map.values()),
            rationale=rationale,
        )
    except Exception as e:
        logger.warning(f"parse_direction_bias failed: {e} | raw={raw_text[:200]}")
        return None


def is_direction_bias_valid(bias: DirectionBias, asof_minute: str) -> bool:
    """Return True if the direction bias has not yet expired."""
    try:
        now_dt = datetime.fromisoformat(asof_minute.replace("Z", "+00:00"))
        valid_until = datetime.fromisoformat(bias.valid_until_minute.replace("Z", "+00:00"))
        return now_dt < valid_until
    except Exception:
        return False


def get_cached_direction_bias(bus) -> Optional[DirectionBias]:
    """Read DirectionBias from Redis cache key."""
    raw = bus.r.get(DIRECTION_BIAS_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return DirectionBias(**data)
    except Exception as e:
        logger.warning(f"get_cached_direction_bias parse error: {e}")
        return None


def store_direction_bias(bus, bias: DirectionBias) -> None:
    """Store DirectionBias in Redis with 70-minute TTL."""
    bus.r.setex(DIRECTION_BIAS_KEY, 4200, json.dumps(bias.model_dump()))


def build_1h_user_payload(fs1h: FeatureSnapshot1h, universe: List[str]) -> dict:
    """Build the user message payload for the 1H LLM direction call."""
    features = {}
    for sym in universe:
        features[sym] = {
            "ret_1h": round(fs1h.ret_1h.get(sym, 0.0), 6),
            "ret_4h": round(fs1h.ret_4h.get(sym, 0.0), 6),
            "vol_1h": round(fs1h.vol_1h.get(sym, 0.0), 6),
            "vol_4h": round(fs1h.vol_4h.get(sym, 0.0), 6),
            "trend_1h": fs1h.trend_1h.get(sym, 0),
            "trend_4h": fs1h.trend_4h.get(sym, 0),
            "trend_agree": fs1h.trend_agree.get(sym, 0),
            "rsi_14_1h": round(fs1h.rsi_14_1h.get(sym, 50.0), 2),
            "aggr_delta_1h": round(fs1h.aggr_delta_1h.get(sym, 0.0), 4),
            "funding_rate": round(fs1h.funding_rate.get(sym, 0.0), 6),
            "basis_bps": round(fs1h.basis_bps.get(sym, 0.0), 2),
            "vol_regime": fs1h.vol_regime.get(sym, 0),
            "book_imbalance_l10": round(fs1h.book_imbalance_l10.get(sym, 0.0), 4),
        }
    return {
        "universe": universe,
        "asof_minute": fs1h.asof_minute,
        "decision_horizon": "4h",
        "features": features,
        "instruction": "Output direction bias JSON per the schema. FLAT when uncertain.",
    }


def process_1h_message(
    fs1h: FeatureSnapshot1h,
    bus,
    llm_cfg,
) -> Optional[DirectionBias]:
    """
    Layer 1: receive a 1H feature snapshot, call LLM, store DirectionBias.
    Returns the DirectionBias if successful, None otherwise.
    """
    # Layer1 去抖动：55分钟内不重复触发，防止连锁高频调仓（参见3/15事故）
    last_ts_raw = bus.r.get(LAYER1_LAST_DECISION_TS_KEY)
    if last_ts_raw:
        try:
            elapsed_min = (time.time() - float(last_ts_raw)) / 60.0
            if elapsed_min < LAYER1_DEBOUNCE_MIN:
                logger.debug(
                    "Layer1 debounce: skip (last=%.1f min ago, threshold=%d min)",
                    elapsed_min, LAYER1_DEBOUNCE_MIN,
                )
                return None
        except (ValueError, TypeError):
            pass  # 无效时间戳，继续正常执行

    user_payload = build_1h_user_payload(fs1h, UNIVERSE)

    raw_response = None
    try:
        if AI_USE_LLM:
            raw_response, _, call_err = call_llm_json(
                llm_cfg,
                system_prompt=SYSTEM_PROMPT_1H,
                user_payload=user_payload,
            )
            if call_err:
                logger.warning("Layer 1 LLM call error: %s", call_err)
                AI_FALLBACK.labels(SERVICE, "layer1_llm_error").inc()
                return None
        elif AI_LLM_MOCK_RESPONSE:
            raw_response = AI_LLM_MOCK_RESPONSE
    except Exception as e:
        logger.warning("Layer 1 LLM call failed: %s", e)
        AI_FALLBACK.labels(SERVICE, "layer1_llm_error").inc()
        return None

    if not raw_response:
        return None

    bias = parse_direction_bias(raw_response, UNIVERSE)
    if bias is None:
        AI_FALLBACK.labels(SERVICE, "layer1_parse_error").inc()
        return None

    bus.r.set(LAYER1_LAST_DECISION_TS_KEY, str(time.time()), ex=LAYER1_DEBOUNCE_TTL_SEC)
    store_direction_bias(bus, bias)
    logger.info(
        "Layer 1 decision | market_state=%s | biases=%s",
        bias.market_state,
        [(b.symbol, b.direction, round(b.confidence, 2)) for b in bias.biases],
    )
    return bias


def apply_direction_confirmation(
    fs: FeatureSnapshot15m,
    bias: DirectionBias,
    universe: List[str],
    min_confirm: int = 3,
) -> Dict[str, float]:
    """
    Layer 2: apply 3-signal confirmation rule to a 1H DirectionBias.
    Returns target weights (positive = LONG, negative = SHORT, 0 = HOLD).
    """
    # Entire market SIDEWAYS or BEARISH → all zero
    if bias.market_state in ("SIDEWAYS", "BEARISH"):
        logger.info("Layer2 decision | market_state=%s all_zero=True", bias.market_state)
        return {sym: 0.0 for sym in universe}

    bias_map = {b.symbol: b for b in bias.biases}
    result = {}

    for sym in universe:
        sym_bias = bias_map.get(sym)
        if not sym_bias or sym_bias.direction == "FLAT":
            result[sym] = 0.0
            continue

        direction = sym_bias.direction
        confirm = 0

        if direction == "LONG":
            if fs.funding_rate.get(sym, 0) > 0:             confirm += 1
            if fs.basis_bps.get(sym, 0) > 0:                confirm += 1
            if fs.oi_change_15m.get(sym, 0) > 0:            confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) > 0.10:     confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) > 0:            confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:     confirm += 1
        else:  # SHORT
            if fs.funding_rate.get(sym, 0) < 0:             confirm += 1
            if fs.basis_bps.get(sym, 0) < 0:                confirm += 1
            if fs.oi_change_15m.get(sym, 0) < 0:            confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) < -0.10:    confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) < 0:            confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:     confirm += 1  # strong trend exists (direction-agnostic)

        if confirm >= min_confirm:
            cap = CAP_BTC_ETH if sym in ("BTC", "ETH") else CAP_ALT
            weight = cap * sym_bias.confidence
            result[sym] = weight if direction == "LONG" else -weight
        else:
            result[sym] = 0.0

        logger.debug(
            "Layer2 | sym=%s dir=%s confirm=%d/%d weight=%.4f",
            sym, direction, confirm, min_confirm, result[sym]
        )

    # 修改3：return 之前添加 INFO 汇总
    active = {s: w for s, w in result.items() if w != 0.0}
    blocked = [
        s for s, w in result.items()
        if w == 0.0 and bias_map.get(s) and bias_map[s].direction != "FLAT"
    ]
    logger.info(
        "Layer2 decision | market_state=%s active=%s blocked_by_confirm=%s",
        bias.market_state, active, blocked
    )
    return result


def apply_profit_target(
    current_weights: Dict[str, float],
    position_pnl_bps: Dict[str, float],
) -> Dict[str, float]:
    """
    主动止盈 / 止损：
    - 当持仓浮动PnL（基点）>= POSITION_PROFIT_TARGET_BPS 时将权重归零（止盈）
    - 当持仓浮动PnL（基点）<= -POSITION_STOP_LOSS_BPS 时将权重归零（止损）

    主循环两阶段接入：
    1. 对 current_w（当前实际持仓）调用本函数，计算出 profit_target_syms 集合
    2. 在 LLM 路径之后将 profit_target_syms 中的 symbol 在 target_w 中归零，
       触发平仓订单，且不会被 LLM 赋值覆盖。
    止盈/止损覆盖发生在 turnover cap 之前，受 turnover cap 约束。

    Args:
        current_weights: 当前持仓权重 {symbol: weight}（非目标权重）
        position_pnl_bps: 各symbol当前浮动PnL基点 {symbol: pnl_bps}

    Returns:
        调整后的权重。触发止盈或止损的symbol权重设为0.0，其余不变。
    """
    result = dict(current_weights)
    for sym, weight in current_weights.items():
        if weight == 0.0:
            continue
        pnl_bps = position_pnl_bps.get(sym, 0.0)
        if pnl_bps >= POSITION_PROFIT_TARGET_BPS:
            logger.info(
                "Profit target hit | sym=%s pnl_bps=%.1f target=%.1f weight %.4f -> 0.0",
                sym, pnl_bps, POSITION_PROFIT_TARGET_BPS, weight
            )
            result[sym] = 0.0
        elif pnl_bps <= -POSITION_STOP_LOSS_BPS:
            logger.warning(
                "Stop loss hit | sym=%s pnl_bps=%.1f stop=%.1f weight %.4f -> 0.0",
                sym, pnl_bps, POSITION_STOP_LOSS_BPS, weight
            )
            result[sym] = 0.0
    return result


def apply_bearish_block(
    target_w: Dict[str, float],
    fs: "FeatureSnapshot15m",
) -> Dict[str, float]:
    """
    局部空头拦截：对 ret_1h < BEARISH_REGIME_RET1H_THRESHOLD 且 trend_agree == 1.0
    的 symbol，将多头权重（正值）强制归零。

    只对 BEARISH_REGIME_LONG_BLOCK=True 时生效（默认True）。
    每个 symbol 独立判断，不影响空头权重（负值）。

    Args:
        target_w: 待调整的目标权重 {symbol: weight}
        fs: 当前 FeatureSnapshot15m（含 ret_1h, trend_agree）

    Returns:
        调整后的目标权重。触发 bearish block 的 symbol 多头权重归零。
    """
    if not BEARISH_REGIME_LONG_BLOCK:
        return target_w
    result = dict(target_w)
    for sym, weight in target_w.items():
        if weight <= 0.0:
            continue  # 空头或零仓位不受影响
        ret_1h_val = fs.ret_1h.get(sym, 0.0)
        trend_agree_val = fs.trend_agree.get(sym, 0.0)
        if ret_1h_val < BEARISH_REGIME_RET1H_THRESHOLD and trend_agree_val == 1.0:
            logger.info(
                "Bearish block | sym=%s ret_1h=%.4f threshold=%.4f trend_agree=%.1f weight %.4f -> 0.0",
                sym, ret_1h_val, BEARISH_REGIME_RET1H_THRESHOLD, trend_agree_val, weight
            )
            result[sym] = 0.0
    return result


# ==================== Capital helpers ====================

def select_max_gross(market_state: str, confidence: float) -> float:
    """Return the appropriate MAX_GROSS based on market state and confidence.
    TRENDING HIGH threshold is MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD (0.70), per spec.
    """
    if market_state in ("SIDEWAYS", "EMERGENCY"):
        return MAX_GROSS_SIDEWAYS
    if market_state == "VOLATILE":
        return MAX_GROSS_VOLATILE
    # TRENDING: use spec-defined threshold of 0.70 for HIGH vs MID
    if confidence >= MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD:   # 0.70
        return MAX_GROSS_TRENDING_HIGH
    return MAX_GROSS_TRENDING_MID


def select_smooth_alpha(confidence: float) -> float:
    """Return EWMA alpha based on confidence: higher confidence -> faster response."""
    if confidence >= MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD:
        return AI_SMOOTH_ALPHA_HIGH   # 0.60
    if confidence >= 0.55:
        return AI_SMOOTH_ALPHA_MID    # 0.40
    return AI_SMOOTH_ALPHA            # 0.25


def apply_min_notional(
    weights: Dict[str, float],
    equity_usd: float,
    min_notional_usd: float = 50.0,
) -> Dict[str, float]:
    """Zero out positions whose notional value is below the minimum threshold."""
    result = {}
    for sym, w in weights.items():
        notional = abs(w) * equity_usd
        result[sym] = w if notional >= min_notional_usd else 0.0
    return result


def get_equity_usd(bus) -> float:
    """Read portfolio equity from cached state snapshot. Returns 1000.0 as safe fallback."""
    try:
        raw = bus.r.get(STATE_KEY)
        if raw:
            data = json.loads(raw)
            return float(data.get("equity_usd", 1000.0))
    except Exception:
        pass
    return 1000.0


DAILY_TRADE_CAP_KEY_PREFIX = "daily_trade_count:"
LAST_TRADE_TS_KEY = "last_trade_timestamp"
LAYER1_LAST_DECISION_TS_KEY = "layer1_last_decision_ts"
LAYER1_DEBOUNCE_MIN = 55  # 55分钟内不重复触发Layer1（防止连锁高频）
LAYER1_DEBOUNCE_TTL_SEC = 3600  # 去抖动key的TTL：1小时，防止进程崩溃后残留key永久阻塞


def get_today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_daily_trade_cap(bus) -> bool:
    """Return True if daily trade cap has been reached (no more trades today)."""
    key = f"{DAILY_TRADE_CAP_KEY_PREFIX}{get_today_utc()}"
    count_raw = bus.r.get(key)
    count = int(count_raw) if count_raw else 0
    return count >= MAX_TRADES_PER_DAY


def increment_daily_trade_count(bus) -> int:
    """Increment today's trade counter, set TTL to expire at end of day."""
    key = f"{DAILY_TRADE_CAP_KEY_PREFIX}{get_today_utc()}"
    count = bus.r.incr(key)
    bus.r.expire(key, 86400)
    return count


def get_minutes_since_last_trade(bus) -> float:
    """Return minutes elapsed since last trade was recorded."""
    raw = bus.r.get(LAST_TRADE_TS_KEY)
    if not raw:
        return 9999.0
    try:
        last_ts = float(raw)
        return (time.time() - last_ts) / 60.0
    except Exception:
        return 9999.0


def record_trade_executed(bus) -> None:
    """Record the timestamp of a trade execution for frequency tracking."""
    bus.r.setex(LAST_TRADE_TS_KEY, 86400, str(time.time()))


# ==================== 主循环 ====================
def main():
    logger.info(f"Starting ai_decision service | STREAM_IN_1H={STREAM_IN_1H} | STREAM_IN={STREAM_IN}")
    start_metrics("METRICS_PORT", 9103)
    bus = RedisStreams(REDIS_URL)

    # Ensure consumer groups exist for both streams
    for stream, group in [(STREAM_IN_1H, GROUP_1H), (STREAM_IN, GROUP)]:
        try:
            bus.r.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception:
            pass  # group already exists

    llm_cfg = LLMConfig(
        endpoint=AI_LLM_ENDPOINT,
        api_key=AI_LLM_API_KEY,
        model=AI_LLM_MODEL,
        timeout_ms=AI_LLM_TIMEOUT_MS,
    )

    error_streak = 0
    alarm_on = False
    # 用于存储上一周期的 mid_px 以检测价格跳水
    last_mid_px: Dict[str, float] = {}
    cached_bias: Optional[DirectionBias] = None

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

    logger.info("Entering dual-loop processing")
    while True:
        poll_env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
        # ── Layer 1: poll md.features.1h (non-blocking) ──────────────────
        msgs_1h = []
        try:
            msgs_1h = bus.xreadgroup_json(
                STREAM_IN_1H,
                GROUP_1H,
                CONSUMER_1H,
                count=5,
                block_ms=LAYER1_POLL_BLOCK_MS,
                recover_pending=False,
            )
        except Exception as e:
            logger.error(f"Layer 1 poll failed: {e}", exc_info=True)
            note_error(poll_env, "layer1_poll", e)
            # Layer 1 timeout must not block Layer 2 decisions.
            msgs_1h = []
        for stream, msg_id, payload in msgs_1h:
            try:
                payload = require_env(payload)
                fs1h = FeatureSnapshot1h(**payload["data"])
                new_bias = process_1h_message(fs1h, bus, llm_cfg)
                if new_bias:
                    cached_bias = new_bias
            except Exception as e:
                logger.warning(f"Layer 1 error: {e}")
                ERR.labels(SERVICE, "layer1").inc()
            finally:
                try:
                    bus.xack(STREAM_IN_1H, GROUP_1H, msg_id)
                except Exception as e:
                    logger.warning(f"Layer 1 xack failed: {e}")
                    ERR.labels(SERVICE, "layer1_ack").inc()

        # ── Layer 2: poll md.features.15m (blocking 5s) ──────────────────
        try:
            msgs_15m = bus.xreadgroup_json(
                STREAM_IN,
                GROUP,
                CONSUMER,
                count=10,
                block_ms=5000,
            )
        except Exception as e:
            logger.error(f"Layer 2 poll failed: {e}", exc_info=True)
            note_error(poll_env, "layer2_poll", e)
            time.sleep(1.0)
            continue
        if not msgs_15m:
            logger.debug("No new messages, waiting...")
        for stream, msg_id, payload in msgs_15m:
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

                # Refresh cached bias from Redis if local copy is None or expired
                if cached_bias is None or not is_direction_bias_valid(cached_bias, fs.asof_minute):
                    cached_bias = get_cached_direction_bias(bus)

                # Check daily trade cap (flag only — don't skip emergency/publish)
                daily_cap_reached = check_daily_trade_cap(bus)
                if daily_cap_reached:
                    logger.info("Daily trade cap reached, will emit HOLD")

                # Minutes-since-last-trade gate
                minutes_since = get_minutes_since_last_trade(bus)

                # Build target weights via Layer 2 confirmation or baseline
                llm_meta: Dict[str, Any] = {}  # Initialize before conditional
                llm_evidence: Dict[str, Any] = {}
                if cached_bias and is_direction_bias_valid(cached_bias, fs.asof_minute):
                    target_w = apply_direction_confirmation(fs, cached_bias, UNIVERSE)
                    market_state = cached_bias.market_state
                    active_confs = [b.confidence for b in cached_bias.biases
                                    if b.direction != "FLAT"]
                    confidence = max(active_confs) if active_confs else 0.0
                    used = "dual_layer_confirmed"
                    rationale = f"layer2_confirmed | market={market_state}"
                    # Mark LLM provider since Layer1 direction_bias comes from LLM
                    llm_meta = {"provider": "online", "layer": "dual", "source": "layer1_llm"}
                else:
                    target_w = build_baseline_weights(fs, UNIVERSE, MAX_GROSS)
                    market_state = "TRENDING"
                    raw_signal = {
                        sym: max(fs.ret_15m.get(sym, 0.0), 0.0) / max(fs.vol_15m.get(sym, 0.01), 0.001)
                        for sym in UNIVERSE
                    }
                    confidence = confidence_from_signals(raw_signal, UNIVERSE)
                    used = "baseline_stabilized"
                    rationale = "baseline 15m mom/vol + confidence gating + EWMA smoothing + turnover cap"

                signal_delta_avg = sum(abs(v) for v in target_w.values()) / len(target_w) if target_w else 0
                logger.info(f"Signal calculation | avg_signal={signal_delta_avg:.4f} | market_state={market_state}")
                raw_llm = None
                llm_cash_weight = None
                llm_parse_error = None

                # Frequency gate: 低置信度时强制最小交易间隔（MIN_TRADE_INTERVAL_MIN）
                if minutes_since < MIN_TRADE_INTERVAL_MIN and confidence < 0.70:
                    logger.info(f"Frequency gate: minutes_since={minutes_since:.1f} conf={confidence:.2f} interval_min={MIN_TRADE_INTERVAL_MIN} -> HOLD")
                    target_w = {sym: 0.0 for sym in UNIVERSE}

                # Daily cap override (after weight building so emergency check below still runs)
                if daily_cap_reached:
                    target_w = {sym: 0.0 for sym in UNIVERSE}

                prev_w, _ = get_prev_target(bus, UNIVERSE)
                current_w = get_current_weights(bus, UNIVERSE)
                logger.info(f"Portfolio state | prev_weights={prev_w} | current_weights={current_w}")

                # 主动止盈/止损：从 STATE_KEY 读取各 symbol 浮动 PnL，
                # 记录触发止盈/止损的 symbol，在 LLM 赋值后重新应用（防止被覆盖）
                position_pnl_bps = get_position_pnl_bps(bus, UNIVERSE)
                profit_adjusted_w = apply_profit_target(current_w, position_pnl_bps)
                profit_target_syms: set = {
                    sym for sym in UNIVERSE
                    if profit_adjusted_w.get(sym, 0.0) == 0.0
                    and current_w.get(sym, 0.0) != 0.0
                }

                asof_dt = datetime.fromisoformat(fs.asof_minute.replace("Z", "+00:00"))
                reversal_counts = {}
                for sym in UNIVERSE:
                    reversal_counts[sym] = get_reversal_counts(bus, sym, DIRECTION_REVERSAL_WINDOW_MIN, asof_dt)

                # 准备盈亏统计数据
                pnl_stats = {}
                for sym in UNIVERSE:
                    pnls = get_recent_pnl(bus, sym, RECENT_PNL_WINDOW)
                    pnl_stats[sym] = {
                        "recent": pnls,
                        "consecutive_loss": check_pnl_penalty(bus, sym),
                        "cumulative_loss": get_cumulative_loss(bus, sym, RECENT_PNL_WINDOW),
                    }

                # LLM candidate (only when no direction bias present and cap not reached)
                if not daily_cap_reached and not (cached_bias and is_direction_bias_valid(cached_bias, fs.asof_minute)):
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

                # 止盈覆盖：在LLM赋值之后重新应用，防止LLM结果覆盖止盈信号
                for sym in profit_target_syms:
                    target_w[sym] = 0.0

                # 局部空头拦截：对下跌趋势 symbol 拦截新多头
                target_w = apply_bearish_block(target_w, fs)

                # 风险调整
                adjusted_confidence = adjust_confidence_by_risk(confidence, fs)

                # Capital utilization: mode-aware MAX_GROSS and EWMA alpha
                eff_max_gross = select_max_gross(market_state, adjusted_confidence)
                eff_smooth_alpha = select_smooth_alpha(adjusted_confidence)
                eff_max_net = MAX_NET_HIGH if adjusted_confidence >= AI_CONFIDENCE_HIGH_THRESHOLD else MAX_NET

                # Determine turnover cap based on confidence mode
                if adjusted_confidence >= AI_CONFIDENCE_HIGH_THRESHOLD:
                    eff_turnover_cap = AI_TURNOVER_CAP_HIGH
                    confidence_mode = "high"
                else:
                    eff_turnover_cap = AI_TURNOVER_CAP
                    confidence_mode = "normal"

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

                # Apply min notional filter
                equity_usd = get_equity_usd(bus)
                candidate_w = apply_min_notional(candidate_w, equity_usd, MIN_NOTIONAL_USD)
                AI_TARGET_ACTUAL_GAP_GROSS.labels(SERVICE).set(
                    sum(abs(candidate_w.get(sym, 0.0) - current_w.get(sym, 0.0)) for sym in UNIVERSE)
                )

                position_target_mismatch, current_gross, prev_target_gross = detect_position_target_mismatch(current_w, prev_w)
                if position_target_mismatch:
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": env.model_dump(),
                                "event": "ai.position_target_mismatch",
                                "data": {
                                    "asof_minute": fs.asof_minute,
                                    "current_gross": current_gross,
                                    "prev_target_gross": prev_target_gross,
                                    "candidate_gross": sum(abs(v) for v in candidate_w.values()),
                                    "epsilon": POSITION_TARGET_MISMATCH_EPSILON,
                                },
                            }
                        ),
                    )

                rebalance, signal_delta, action_reason = should_rebalance(bus, prev_w, current_w, candidate_w, fs.asof_minute)
                # Daily cap: force HOLD regardless of what should_rebalance decided
                if daily_cap_reached:
                    rebalance = False
                    action_reason = "daily_trade_cap_reached"
                final_w = candidate_w if rebalance else prev_w
                decision_action = "REBALANCE" if rebalance else "HOLD"
                logger.info(f"Decision | action={decision_action} | signal_delta={signal_delta:.4f} | reason={action_reason} | adjusted_confidence={adjusted_confidence:.2f}")

                # Track trade execution
                if decision_action == "REBALANCE":
                    daily_count = increment_daily_trade_count(bus)
                    record_trade_executed(bus)
                    # 每次 REBALANCE 发出 gauge（注意：1 次 REBALANCE 对应多笔交易所订单）
                    DAILY_TRADE_COUNT.labels(SERVICE).set(daily_count)
                    if daily_count > MAX_TRADES_PER_DAY * 0.8:
                        set_alarm("daily_trade_count_high", True)

                gross = sum(abs(v) for v in final_w.values())
                net = sum(final_w.values())
                AI_CONFIDENCE.labels(SERVICE).set(confidence)
                AI_GROSS.labels(SERVICE).set(gross)
                AI_NET.labels(SERVICE).set(net)
                AI_TURNOVER.labels(SERVICE).set(turnover_after)
                major_cycle = to_major_cycle_id(fs.asof_minute)
                evidence = {
                    "regime": "dual_layer",
                    "market_state": market_state,
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
                    model={"name": used, "version": "dual_layer_v1"},
                    constraints_hint={
                        "max_gross": eff_max_gross,
                        "max_net": eff_max_net,
                        "cap_btc_eth": CAP_BTC_ETH,
                        "cap_alt": CAP_ALT,
                        "turnover_cap": eff_turnover_cap,
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
                                "market_state": market_state,
                                "gross": gross,
                                "net_before": net_before,
                                "net_after": net_after,
                                "cash_weight_in": cash_weight_in,
                                "turnover_before": turnover_before,
                                "turnover_after": turnover_after,
                                "smooth_alpha": eff_smooth_alpha,
                                "confidence_threshold": AI_MIN_CONFIDENCE,
                                "turnover_cap": eff_turnover_cap,
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
