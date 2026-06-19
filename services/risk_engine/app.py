import os
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict, Any, Literal, cast
from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.schemas import Envelope, TargetPortfolio, StateSnapshot, ApprovedTargetPortfolio, TargetWeight, Rejection, current_cycle_id
from pydantic import ValidationError
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.metrics.prom import (
    start_metrics,
    MSG_IN,
    MSG_OUT,
    ERR,
    LAT,
    set_alarm,
    RISK_CLIP,
    RISK_NET_HITS,
    RISK_GROSS_HITS,
    RISK_TURNOVER_HITS,
)
from shared.v17_live_canary_config import apply_if_enabled

apply_if_enabled("risk")

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

PRODUCTION_TARGET_STREAM = "alpha.target"
V17_TARGET_STREAM = "alpha.target.v17_shadow"
V17_RISK_APPROVED_STREAM = "risk.approved.v17_shadow"
V17_LIVE_TARGET_STREAM = "alpha.target.v17_live"
V17_LIVE_RISK_APPROVED_STREAM = "risk.approved.v17_live"
V17_DRY_RUN_INTEGRATION = os.environ.get("V17_DRY_RUN_INTEGRATION", "false").lower() == "true"
V17_LIVE_EXECUTION = os.environ.get("V17_LIVE_EXECUTION", "false").lower() == "true"
V17_LIVE_REAL_MONEY_APPROVED = os.environ.get("V17_LIVE_REAL_MONEY_APPROVED", "false").lower() == "true"
STREAM_IN = V17_LIVE_TARGET_STREAM if V17_LIVE_EXECUTION else (V17_TARGET_STREAM if V17_DRY_RUN_INTEGRATION else PRODUCTION_TARGET_STREAM)
STREAM_STATE_KEY = "latest.state.snapshot"  # Redis key maintained by portfolio_state
STREAM_OUT = V17_LIVE_RISK_APPROVED_STREAM if V17_LIVE_EXECUTION else (V17_RISK_APPROVED_STREAM if V17_DRY_RUN_INTEGRATION else "risk.approved")
AUDIT = "audit.logs"
CTL_MODE_KEY = "ctl.mode"

if V17_DRY_RUN_INTEGRATION and STREAM_IN == PRODUCTION_TARGET_STREAM:
    raise RuntimeError("V17 dry-run integration must not consume production alpha.target")
if V17_LIVE_EXECUTION and not V17_LIVE_REAL_MONEY_APPROVED:
    raise RuntimeError("V17 live execution requires explicit real-money approval")
if V17_LIVE_EXECUTION and STREAM_IN == PRODUCTION_TARGET_STREAM:
    raise RuntimeError("V17 live execution must not consume production alpha.target")

GROUP = "risk_grp"
CONSUMER = os.environ.get("CONSUMER", "risk_1")
RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))
STATE_STALE_SECS = int(os.environ.get("STATE_STALE_SECS", "45"))
DAILY_LOSS_REDUCE_ONLY_PCT = float(os.environ.get("DAILY_LOSS_REDUCE_ONLY_PCT", "0.03"))
DAILY_LOSS_HALT_PCT = float(os.environ.get("DAILY_LOSS_HALT_PCT", "0.05"))
CONSECUTIVE_REJECTED_REDUCE_ONLY = int(os.environ.get("CONSECUTIVE_REJECTED_REDUCE_ONLY", "8"))
CONSECUTIVE_REJECTED_HALT = int(os.environ.get("CONSECUTIVE_REJECTED_HALT", "20"))
# Hard cap to avoid large reverse-range scans under stress.
EXEC_REPORT_SCAN_LIMIT = min(int(os.environ.get("EXEC_REPORT_SCAN_LIMIT", "200")), 200)
MARKET_FEATURE_STREAM = os.environ.get("RISK_MARKET_FEATURE_STREAM", "md.features.15m")
RISK_REJECT_RATE_REDUCE_ONLY = float(os.environ.get("RISK_REJECT_RATE_REDUCE_ONLY", "0.15"))
RISK_REJECT_RATE_HALT = float(os.environ.get("RISK_REJECT_RATE_HALT", "0.35"))
RISK_P95_LATENCY_MS_REDUCE_ONLY = float(os.environ.get("RISK_P95_LATENCY_MS_REDUCE_ONLY", "3000"))
RISK_P95_LATENCY_MS_HALT = float(os.environ.get("RISK_P95_LATENCY_MS_HALT", "7000"))
RISK_LIQUIDITY_SCORE_REDUCE_ONLY = float(os.environ.get("RISK_LIQUIDITY_SCORE_REDUCE_ONLY", "0.15"))
RISK_LIQUIDITY_SCORE_HALT = float(os.environ.get("RISK_LIQUIDITY_SCORE_HALT", "0.05"))
RISK_BASIS_BPS_REDUCE_ONLY = float(os.environ.get("RISK_BASIS_BPS_REDUCE_ONLY", "40"))
RISK_BASIS_BPS_HALT = float(os.environ.get("RISK_BASIS_BPS_HALT", "80"))
RISK_OI_CHANGE_REDUCE_ONLY = float(os.environ.get("RISK_OI_CHANGE_REDUCE_ONLY", "0.20"))
RISK_OI_CHANGE_HALT = float(os.environ.get("RISK_OI_CHANGE_HALT", "0.40"))
# M1: Peak drawdown guard — halt when equity drops >= pct from all-time session peak
PEAK_DRAWDOWN_HALT_PCT = float(os.environ.get("PEAK_DRAWDOWN_HALT_PCT", "0.08"))
# M1: IC decay guard — reduce-only when rolling IC_1h < threshold for N consecutive observations
IC_DECAY_THRESHOLD = float(os.environ.get("IC_DECAY_THRESHOLD", "-0.03"))
IC_DECAY_STREAK_REDUCE_ONLY = int(os.environ.get("IC_DECAY_STREAK_REDUCE_ONLY", "3"))

SERVICE = "risk_engine"
os.environ["SERVICE_NAME"] = SERVICE
logger = logging.getLogger(__name__)

def cap_for(sym: str) -> float:
    if sym in ("BTC", "ETH"):
        return float(os.environ.get("CAP_BTC_ETH", "0.15"))
    return float(os.environ.get("CAP_ALT", "0.10"))


def _scale_to_gross_net(targets: List[TargetWeight], max_gross: float, max_net: float) -> Tuple[List[TargetWeight], float, float, float]:
    gross = sum(abs(x.weight) for x in targets)
    net = sum(x.weight for x in targets)
    scale = 1.0
    if gross > max_gross + 1e-12:
        scale = max_gross / gross
    if abs(net) > max_net + 1e-12:
        scale = min(scale, max_net / abs(net))
    if scale < 0.999:
        targets = [TargetWeight(symbol=x.symbol, weight=x.weight * scale) for x in targets]
    return targets, scale, gross, net


def apply_position_limits(
    targets: List[TargetWeight],
    *,
    current_weights: Dict[str, float],
    max_gross: float,
    max_net: float,
    turnover_cap: float,
) -> Tuple[List[TargetWeight], List[Rejection]]:
    rejections: List[Rejection] = []
    approved: List[TargetWeight] = []

    for tw in targets:
        cap = cap_for(tw.symbol)
        w0 = tw.weight
        w1 = max(min(w0, cap), -cap)
        if abs(w1 - w0) > 1e-12:
            rejections.append(Rejection(symbol=tw.symbol, reason="per_symbol_cap", original_weight=w0, approved_weight=w1))
            RISK_CLIP.labels(SERVICE, "per_symbol_cap").inc()
        approved.append(TargetWeight(symbol=tw.symbol, weight=w1))

    approved, scale, gross, _ = _scale_to_gross_net(approved, max_gross, max_net)
    if scale < 0.999:
        rejections.append(Rejection(symbol="*", reason="gross_or_net_scaled", original_weight=gross, approved_weight=sum(abs(x.weight) for x in approved)))
        RISK_CLIP.labels(SERVICE, "gross_or_net_scaled").inc()
        if gross > max_gross + 1e-12:
            RISK_GROSS_HITS.labels(SERVICE).inc()
        if abs(sum(x.weight for x in approved)) > max_net + 1e-12:
            RISK_NET_HITS.labels(SERVICE).inc()

    tgt = {x.symbol: x.weight for x in approved}
    symbols = set(current_weights) | set(tgt)
    turnover = sum(abs(tgt.get(sym, 0.0) - current_weights.get(sym, 0.0)) for sym in symbols)
    if turnover > turnover_cap + 1e-12:
        RISK_TURNOVER_HITS.labels(SERVICE).inc()
        factor = turnover_cap / turnover
        adjusted = []
        for x in approved:
            sym = x.symbol
            w = current_weights.get(sym, 0.0) + (tgt.get(sym, 0.0) - current_weights.get(sym, 0.0)) * factor
            adjusted.append(TargetWeight(symbol=sym, weight=w))
        rejections.append(Rejection(symbol="*", reason="turnover_scaled", original_weight=turnover, approved_weight=turnover_cap))
        RISK_CLIP.labels(SERVICE, "turnover_scaled").inc()
        approved = adjusted

    post_cap: List[TargetWeight] = []
    for tw in approved:
        cap = cap_for(tw.symbol)
        w1 = max(min(tw.weight, cap), -cap)
        if abs(w1 - tw.weight) > 1e-12:
            rejections.append(Rejection(symbol=tw.symbol, reason="post_turnover_cap", original_weight=tw.weight, approved_weight=w1))
            RISK_CLIP.labels(SERVICE, "post_turnover_cap").inc()
        post_cap.append(TargetWeight(symbol=tw.symbol, weight=w1))
    approved = post_cap

    approved, post_scale, post_gross, _ = _scale_to_gross_net(approved, max_gross, max_net)
    if post_scale < 0.999:
        rejections.append(Rejection(symbol="*", reason="post_turnover_gross_or_net_scaled", original_weight=post_gross, approved_weight=sum(abs(x.weight) for x in approved)))
        RISK_CLIP.labels(SERVICE, "post_turnover_gross_or_net_scaled").inc()

    return approved, rejections


def enforce_live_target_approval(
    target: TargetPortfolio,
    approved: List[TargetWeight],
    *,
    require_live_approval: bool,
) -> Tuple[str, Literal["REBALANCE", "HOLD", "PROTECTIVE_ONLY"], List[TargetWeight], List[Rejection]]:
    if not require_live_approval:
        return "NORMAL", target.decision_action, approved, []

    evidence = target.evidence if isinstance(target.evidence, dict) else {}
    if evidence.get("real_money_execution_allowed") is True:
        return "NORMAL", target.decision_action, approved, []

    return (
        "HALT",
        "HOLD",
        [],
        [
            Rejection(
                symbol="*",
                reason="live_target_real_money_not_allowed",
                original_weight=0.0,
                approved_weight=0.0,
            )
        ],
    )


def risk_effective_leverage(constraints_hint: Dict[str, Any]) -> float:
    try:
        leverage = float(constraints_hint.get("effective_leverage", os.environ.get("POSITION_LEVERAGE", "1")))
    except (TypeError, ValueError):
        leverage = float(os.environ.get("POSITION_LEVERAGE", "1"))
    return max(leverage, 1e-9)


def current_position_weights(
    st: Optional[StateSnapshot],
    universe: List[str],
    *,
    leverage: float,
) -> Dict[str, float]:
    if st is None:
        return {}
    eq = max(st.equity_usd, 1e-6)
    lev = max(float(leverage), 1e-9)
    weights: Dict[str, float] = {}
    for sym in universe:
        pos = st.positions.get(sym)
        weights[sym] = 0.0 if not pos else (pos.qty * pos.mark_px) / (eq * lev)
    return weights


def finalize_risk_mode_targets(
    mode: str,
    decision_action: Literal["REBALANCE", "HOLD", "PROTECTIVE_ONLY"],
    approved: List[TargetWeight],
    *,
    current_weights: Dict[str, float],
) -> Tuple[Literal["REBALANCE", "HOLD", "PROTECTIVE_ONLY"], List[TargetWeight]]:
    if mode == "HALT":
        return "HOLD", []
    if mode != "REDUCE_ONLY":
        return decision_action, approved

    constrained: List[TargetWeight] = []
    for tw in approved:
        current_weight = current_weights.get(tw.symbol, 0.0)
        if abs(tw.weight) > abs(current_weight) + 1e-12:
            constrained.append(TargetWeight(symbol=tw.symbol, weight=current_weight))
        else:
            constrained.append(tw)
    return decision_action, constrained


def get_control_mode(bus: RedisStreams) -> str:
    getter = getattr(bus, "get", None)
    redis_client = getattr(bus, "r", None)
    redis_getter = getattr(redis_client, "get", None)

    def _raw_get() -> Any:
        if callable(getter):
            return getter(CTL_MODE_KEY)
        if callable(redis_getter):
            return redis_getter(CTL_MODE_KEY)
        return None

    try:
        raw = bus.get_json(CTL_MODE_KEY)
    except ValueError:
        raw = _raw_get()
    if not raw:
        raw = _raw_get()
    if not raw:
        return "NORMAL"
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        mode = raw.upper()
    else:
        raw_dict = cast(Dict[str, Any], raw)
        mode = str(raw_dict.get("mode", "NORMAL")).upper()
    if mode not in {"NORMAL", "REDUCE_ONLY", "HALT"}:
        return "NORMAL"
    return mode


def parse_utc_ts(value: str) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None

def health_mode(state_env: dict, st: Optional[StateSnapshot], stale_secs: int, require_live_source: bool = False) -> Tuple[str, List[Rejection]]:
    if st is None:
        return "HALT", [Rejection(symbol="*", reason="state_missing", original_weight=0.0, approved_weight=0.0)]

    rejections: List[Rejection] = []
    health = st.health if isinstance(st.health, dict) else {}
    if require_live_source and health.get("mode") != "live_exchange":
        return "HALT", [Rejection(symbol="*", reason="paper_shadow_state_in_live", original_weight=0.0, approved_weight=0.0)]
    if require_live_source:
        min_live_equity = float(os.environ.get("V17_MIN_LIVE_EQUITY_USD", "10"))
        if st.equity_usd < min_live_equity:
            return "HALT", [
                Rejection(
                    symbol="*",
                    reason="live_equity_below_floor",
                    original_weight=st.equity_usd,
                    approved_weight=min_live_equity,
                )
            ]
    reconcile_ok = health.get("reconcile_ok", True)
    if reconcile_ok is False:
        return "HALT", [Rejection(symbol="*", reason="reconcile_unhealthy", original_weight=0.0, approved_weight=0.0)]

    ts = parse_utc_ts(str(health.get("last_reconcile_ts", "")))
    if ts is None:
        ts = parse_utc_ts(str(state_env.get("ts", "")))
    if ts is not None:
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > stale_secs:
            rejections.append(Rejection(symbol="*", reason="state_stale_reduce_only", original_weight=age, approved_weight=float(stale_secs)))
            return "REDUCE_ONLY", rejections
    return "NORMAL", rejections

def merge_modes(base_mode: str, control_mode: str) -> str:
    if control_mode == "HALT":
        return "HALT"
    if control_mode == "REDUCE_ONLY":
        return "REDUCE_ONLY" if base_mode == "NORMAL" else base_mode
    return base_mode


def escalate_mode(*modes: str) -> str:
    score = {"NORMAL": 0, "REDUCE_ONLY": 1, "HALT": 2}
    best = "NORMAL"
    for m in modes:
        if score.get(m, 0) > score.get(best, 0):
            best = m
    return best


def calc_daily_loss_ratio(baseline_equity: float, current_equity: float) -> float:
    base = max(float(baseline_equity), 1e-9)
    cur = float(current_equity)
    return max(0.0, (base - cur) / base)


def mode_from_daily_loss(loss_ratio: float, reduce_only_pct: float, halt_pct: float) -> Tuple[str, str]:
    if halt_pct > 0 and loss_ratio >= halt_pct:
        return "HALT", "daily_loss_halt"
    if reduce_only_pct > 0 and loss_ratio >= reduce_only_pct:
        return "REDUCE_ONLY", "daily_loss_reduce_only"
    return "NORMAL", ""


def consecutive_rejected_streak(statuses: List[str]) -> int:
    c = 0
    for s in statuses:
        if s == "REJECTED":
            c += 1
        else:
            break
    return c


def mode_from_reject_streak(streak: int, reduce_only_n: int, halt_n: int) -> Tuple[str, str]:
    if halt_n > 0 and streak >= halt_n:
        return "HALT", "consecutive_rejected_halt"
    if reduce_only_n > 0 and streak >= reduce_only_n:
        return "REDUCE_ONLY", "consecutive_rejected_reduce_only"
    return "NORMAL", ""


def _decode_stream_payload(fields: Dict[str, Any]) -> Dict[str, Any]:
    raw = fields.get("p")
    if not isinstance(raw, str):
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def recent_exec_statuses(bus: RedisStreams, limit: int) -> List[str]:
    rows = bus.r.xrevrange("exec.reports", count=max(1, int(limit)))  # type: ignore[arg-type]
    out: List[str] = []
    for _, fields in rows:
        payload = _decode_stream_payload(fields)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        status = data.get("status") if isinstance(data, dict) else None
        if isinstance(status, str):
            out.append(status)
    return out

def latest_market_features(bus: RedisStreams, stream: str) -> Dict[str, Any]:
    rows = bus.r.xrevrange(stream, count=1)  # type: ignore[arg-type]
    if not rows:
        return {}
    _, fields = rows[0]
    payload = _decode_stream_payload(fields)
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _feature_price(market_features: Dict[str, Any], symbol: str) -> Optional[float]:
    for field in ("mark_px", "mid_px", "mark_price", "mid_price", "price"):
        values = market_features.get(field)
        if isinstance(values, dict):
            value = values.get(symbol)
            if isinstance(value, (int, float)) and float(value) > 0:
                return float(value)
        elif symbol in UNIVERSE and isinstance(values, (int, float)) and float(values) > 0:
            return float(values)
    return None


def build_constraints_hint_with_mark_prices(
    constraints_hint: Dict[str, float],
    *,
    state: Optional[StateSnapshot],
    market_features: Dict[str, Any],
    universe: List[str],
) -> Dict[str, float]:
    hints: Dict[str, float] = dict(constraints_hint or {})
    for symbol in universe:
        price: Optional[float] = None
        if state is not None:
            position = state.positions.get(symbol)
            if position is not None and position.mark_px > 0:
                price = float(position.mark_px)
        if price is None:
            price = _feature_price(market_features, symbol)
        if price is not None and price > 0:
            hints[f"mark_px_{symbol}"] = float(price)
    return hints


def safe_xreadgroup_json(
    bus: RedisStreams,
    stream: str,
    group: str,
    consumer: str,
    *,
    count: int,
    block_ms: int,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    try:
        return bus.xreadgroup_json(stream, group, consumer, count=count, block_ms=block_ms)
    except Exception as e:
        logger.warning(
            "stream poll failed, continue loop | stream=%s group=%s consumer=%s err=%s",
            stream,
            group,
            consumer,
            e,
        )
        return []


def _avg_values(d: Any, universe: List[str]) -> float:
    if not isinstance(d, dict):
        return 0.0
    vals: List[float] = []
    for sym in universe:
        v = d.get(sym)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _max_abs_values(d: Any, universe: List[str]) -> float:
    if not isinstance(d, dict):
        return 0.0
    vals: List[float] = []
    for sym in universe:
        v = d.get(sym)
        if isinstance(v, (int, float)):
            vals.append(abs(float(v)))
    if not vals:
        return 0.0
    return max(vals)


def mode_from_market_quality(features: Dict[str, Any], universe: List[str]) -> Tuple[str, List[str], Dict[str, float]]:
    reject_rate_avg = _avg_values(features.get("reject_rate_15m"), universe)
    p95_latency_avg = _avg_values(features.get("p95_latency_ms_15m"), universe)
    liquidity_avg = _avg_values(features.get("liquidity_score"), universe)
    basis_abs_avg = _avg_values({k: abs(v) for k, v in (features.get("basis_bps") or {}).items()} if isinstance(features.get("basis_bps"), dict) else {}, universe)
    oi_change_abs_max = _max_abs_values(features.get("oi_change_15m"), universe)

    stats = {
        "reject_rate_avg": reject_rate_avg,
        "p95_latency_avg": p95_latency_avg,
        "liquidity_avg": liquidity_avg,
        "basis_abs_avg": basis_abs_avg,
        "oi_change_abs_max": oi_change_abs_max,
    }

    reasons: List[str] = []
    mode = "NORMAL"

    if reject_rate_avg >= RISK_REJECT_RATE_HALT:
        mode = "HALT"
        reasons.append("execution_reject_rate_halt")
    elif reject_rate_avg >= RISK_REJECT_RATE_REDUCE_ONLY:
        mode = escalate_mode(mode, "REDUCE_ONLY")
        reasons.append("execution_reject_rate_reduce_only")

    if p95_latency_avg >= RISK_P95_LATENCY_MS_HALT:
        mode = "HALT"
        reasons.append("execution_latency_halt")
    elif p95_latency_avg >= RISK_P95_LATENCY_MS_REDUCE_ONLY:
        mode = escalate_mode(mode, "REDUCE_ONLY")
        reasons.append("execution_latency_reduce_only")

    if liquidity_avg <= RISK_LIQUIDITY_SCORE_HALT and liquidity_avg > 0:
        mode = "HALT"
        reasons.append("liquidity_halt")
    elif liquidity_avg <= RISK_LIQUIDITY_SCORE_REDUCE_ONLY and liquidity_avg > 0:
        mode = escalate_mode(mode, "REDUCE_ONLY")
        reasons.append("liquidity_reduce_only")

    if basis_abs_avg >= RISK_BASIS_BPS_HALT:
        mode = "HALT"
        reasons.append("basis_halt")
    elif basis_abs_avg >= RISK_BASIS_BPS_REDUCE_ONLY:
        mode = escalate_mode(mode, "REDUCE_ONLY")
        reasons.append("basis_reduce_only")

    if oi_change_abs_max >= RISK_OI_CHANGE_HALT:
        mode = "HALT"
        reasons.append("oi_change_halt")
    elif oi_change_abs_max >= RISK_OI_CHANGE_REDUCE_ONLY:
        mode = escalate_mode(mode, "REDUCE_ONLY")
        reasons.append("oi_change_reduce_only")

    return mode, reasons, stats


def daily_equity_baseline(bus: RedisStreams, current_equity: float, now: datetime) -> float:
    day = now.strftime("%Y%m%d")
    key = f"risk.daily_baseline.{day}"
    cur = max(float(current_equity), 1e-9)
    source = "risk_engine.daily_equity_baseline"
    schema_version = 2
    got = bus.get_json(key)
    if got and isinstance(got, dict):
        val = got.get("equity_usd")
        if (
            got.get("source") == source
            and got.get("schema_version") == schema_version
            and isinstance(val, (int, float))
            and float(val) > 0
        ):
            return float(val)
    bus.set_json(
        key,
        {
            "equity_usd": cur,
            "ts": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": source,
            "schema_version": schema_version,
        },
        ex=3 * 24 * 3600,
    )
    return cur

def peak_equity_guard(bus: RedisStreams, current_equity: float) -> Tuple[str, str, float, float]:
    """
    Track rolling session-peak equity.  Return HALT when peak drawdown >= PEAK_DRAWDOWN_HALT_PCT.
    Peak is persisted in Redis (TTL 30 days) so it survives service restarts.
    Returns (mode, reason, peak_equity, drawdown_ratio).
    """
    key = "risk.peak_equity"
    cur = max(float(current_equity), 1e-9)
    got = bus.get_json(key)
    peak = cur
    if got and isinstance(got, dict):
        stored = got.get("equity_usd")
        if isinstance(stored, (int, float)) and float(stored) > 0:
            peak = float(stored)
    if cur > peak:
        peak = cur
        bus.set_json(
            key,
            {"equity_usd": peak, "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
            ex=30 * 86400,
        )
    drawdown = max(0.0, (peak - cur) / peak)
    if PEAK_DRAWDOWN_HALT_PCT > 0 and drawdown >= PEAK_DRAWDOWN_HALT_PCT:
        return "HALT", "peak_drawdown_halt", peak, drawdown
    return "NORMAL", "", peak, drawdown


def ic_decay_guard(bus: RedisStreams) -> Tuple[str, str, int]:
    """
    Read rolling IC_1h from ic.signal_ic_latest (written hourly by health report).
    Track consecutive observations below IC_DECAY_THRESHOLD in risk.ic_decay_streak.
    Returns (mode, reason, streak_count).
    """
    ic_raw = bus.get_json("ic.signal_ic_latest")
    if not ic_raw or not isinstance(ic_raw, dict):
        return "NORMAL", "", 0

    ic_1h_val = ic_raw.get("ic_1h")
    if ic_1h_val is None:
        return "NORMAL", "", 0

    ic_1h = float(ic_1h_val)
    ic_ts = str(ic_raw.get("ts", ""))

    # Update streak only when we see a new IC observation (keyed by its timestamp)
    streak_raw = bus.get_json("risk.ic_decay_streak")
    streak = 0
    last_ts = ""
    if streak_raw and isinstance(streak_raw, dict):
        streak = int(streak_raw.get("streak", 0))
        last_ts = str(streak_raw.get("last_ic_ts", ""))

    if ic_ts and ic_ts != last_ts:
        streak = (streak + 1) if ic_1h < IC_DECAY_THRESHOLD else 0
        bus.set_json(
            "risk.ic_decay_streak",
            {"streak": streak, "last_ic_ts": ic_ts, "ic_1h": ic_1h},
            ex=7 * 86400,
        )

    if IC_DECAY_STREAK_REDUCE_ONLY > 0 and streak >= IC_DECAY_STREAK_REDUCE_ONLY:
        return "REDUCE_ONLY", "ic_decay_reduce_only", streak
    return "NORMAL", "", streak


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
        msgs = safe_xreadgroup_json(
            bus,
            STREAM_IN,
            GROUP,
            CONSUMER,
            count=20,
            block_ms=5000,
        )
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
                state_env = {}
                if st_raw:
                    state_env = st_raw.get("env", {}) if isinstance(st_raw, dict) else {}
                    try:
                        st = StateSnapshot(**st_raw["data"])
                    except ValidationError as e:
                        env = Envelope(source="risk_engine", cycle_id=incoming_env.cycle_id)
                        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "schema_error", "schema": "StateSnapshot", "reason": str(e), "data": st_raw.get("data")}))
                        ERR.labels(SERVICE, "schema").inc()
                        note_error(env, "schema", e)
                        st = None

                mode, mode_rejections = health_mode(
                    state_env,
                    st,
                    STATE_STALE_SECS,
                    require_live_source=V17_LIVE_EXECUTION,
                )

                # Hard risk protection 1: daily drawdown guard from equity baseline.
                daily_loss_ratio = 0.0
                daily_loss_mode = "NORMAL"
                if st is not None and st.equity_usd > 0:
                    baseline = daily_equity_baseline(bus, st.equity_usd, datetime.now(timezone.utc))
                    daily_loss_ratio = calc_daily_loss_ratio(baseline, st.equity_usd)
                    daily_loss_mode, daily_loss_reason = mode_from_daily_loss(
                        daily_loss_ratio,
                        DAILY_LOSS_REDUCE_ONLY_PCT,
                        DAILY_LOSS_HALT_PCT,
                    )
                    if daily_loss_mode != "NORMAL":
                        mode_rejections.append(
                            Rejection(
                                symbol="*",
                                reason=daily_loss_reason,
                                original_weight=daily_loss_ratio,
                                approved_weight=0.0,
                            )
                        )
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": incoming_env.model_dump(),
                                    "event": "risk.guard.daily_loss",
                                    "data": {
                                        "daily_loss_ratio": daily_loss_ratio,
                                        "mode": daily_loss_mode,
                                        "baseline_equity_usd": baseline,
                                        "current_equity_usd": st.equity_usd,
                                    },
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()

                # Hard risk protection 2: peak equity drawdown guard (M1).
                peak_mode = "NORMAL"
                peak_equity_usd = 0.0
                peak_drawdown_ratio = 0.0
                if st is not None and st.equity_usd > 0:
                    peak_mode, peak_reason, peak_equity_usd, peak_drawdown_ratio = peak_equity_guard(
                        bus, st.equity_usd
                    )
                    if peak_mode != "NORMAL":
                        mode_rejections.append(
                            Rejection(
                                symbol="*",
                                reason=peak_reason,
                                original_weight=peak_drawdown_ratio,
                                approved_weight=0.0,
                            )
                        )
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": incoming_env.model_dump(),
                                    "event": "risk.guard.peak_drawdown",
                                    "data": {
                                        "peak_equity_usd": peak_equity_usd,
                                        "current_equity_usd": st.equity_usd,
                                        "peak_drawdown_ratio": peak_drawdown_ratio,
                                        "mode": peak_mode,
                                    },
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()

                # Hard risk protection 3: IC decay guard (M1).
                ic_mode, ic_reason, ic_streak = ic_decay_guard(bus)
                if ic_mode != "NORMAL":
                    mode_rejections.append(
                        Rejection(
                            symbol="*",
                            reason=ic_reason,
                            original_weight=float(ic_streak),
                            approved_weight=0.0,
                        )
                    )
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": incoming_env.model_dump(),
                                "event": "risk.guard.ic_decay",
                                "data": {"ic_streak": ic_streak, "threshold": IC_DECAY_THRESHOLD, "mode": ic_mode},
                            }
                        ),
                    )
                    MSG_OUT.labels(SERVICE, AUDIT).inc()

                # Hard risk protection 4: consecutive execution rejects guard.
                statuses = recent_exec_statuses(bus, EXEC_REPORT_SCAN_LIMIT)
                reject_streak = consecutive_rejected_streak(statuses)
                reject_mode, reject_reason = mode_from_reject_streak(
                    reject_streak,
                    CONSECUTIVE_REJECTED_REDUCE_ONLY,
                    CONSECUTIVE_REJECTED_HALT,
                )
                if reject_mode != "NORMAL":
                    mode_rejections.append(
                        Rejection(
                            symbol="*",
                            reason=reject_reason,
                            original_weight=float(reject_streak),
                            approved_weight=0.0,
                        )
                    )
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": incoming_env.model_dump(),
                                "event": "risk.guard.reject_streak",
                                "data": {
                                    "streak": reject_streak,
                                    "mode": reject_mode,
                                    "scan_limit": EXEC_REPORT_SCAN_LIMIT,
                                },
                            }
                        ),
                    )
                    MSG_OUT.labels(SERVICE, AUDIT).inc()

                market_features = latest_market_features(bus, MARKET_FEATURE_STREAM)
                quality_mode, quality_reasons, quality_stats = mode_from_market_quality(market_features, UNIVERSE)
                if quality_mode != "NORMAL":
                    for reason in quality_reasons:
                        mode_rejections.append(
                            Rejection(
                                symbol="*",
                                reason=reason,
                                original_weight=0.0,
                                approved_weight=0.0,
                            )
                        )
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": incoming_env.model_dump(),
                                "event": "risk.guard.market_quality",
                                "data": {
                                    "mode": quality_mode,
                                    "reasons": quality_reasons,
                                    "stats": quality_stats,
                                },
                            }
                        ),
                    )
                    MSG_OUT.labels(SERVICE, AUDIT).inc()

                mode = escalate_mode(mode, daily_loss_mode, peak_mode, ic_mode, reject_mode, quality_mode)
                control_mode = get_control_mode(bus)
                mode = merge_modes(mode, control_mode)

                current_weights = current_position_weights(
                    st,
                    UNIVERSE,
                    leverage=risk_effective_leverage(tp.constraints_hint),
                )
                approved, rejections = apply_position_limits(
                    tp.targets,
                    current_weights=current_weights,
                    max_gross=max_gross,
                    max_net=max_net,
                    turnover_cap=turnover_cap,
                )

                approval_mode, decision_action, approved, approval_rejections = enforce_live_target_approval(
                    tp,
                    approved,
                    require_live_approval=V17_LIVE_EXECUTION,
                )
                mode = escalate_mode(mode, approval_mode)
                rejections.extend(approval_rejections)

                # Hard modes must be explicit and fail-closed before publishing.
                decision_action, approved = finalize_risk_mode_targets(
                    mode,
                    decision_action,
                    approved,
                    current_weights=current_weights,
                )

                rejections.extend(mode_rejections)

                constraints_hint = build_constraints_hint_with_mark_prices(
                    tp.constraints_hint,
                    state=st,
                    market_features=market_features,
                    universe=UNIVERSE,
                )

                ap = ApprovedTargetPortfolio(
                    asof_minute=tp.asof_minute,
                    mode=mode,
                    decision_action=decision_action,
                    approved_targets=approved,
                    rejections=rejections,
                    constraints_hint=constraints_hint,
                    risk_summary={
                        "gross": sum(abs(x.weight) for x in approved),
                        "net": sum(x.weight for x in approved),
                        "control_mode": control_mode,
                        "daily_loss_ratio": daily_loss_ratio,
                        "peak_equity_usd": peak_equity_usd,
                        "peak_drawdown_ratio": peak_drawdown_ratio,
                        "ic_decay_streak": ic_streak,
                        "consecutive_rejected": reject_streak,
                        "market_quality_mode": quality_mode,
                        "market_quality_reasons": quality_reasons,
                        "market_quality_stats": quality_stats,
                    },
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
