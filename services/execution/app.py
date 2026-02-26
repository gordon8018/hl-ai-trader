# services/execution/app.py
import os
import time
import math
import httpx
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Optional, List, Tuple

from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.schemas import (
    Envelope, ApprovedTargetPortfolio, ExecutionLimits, StateSnapshot,
    ExecutionPlan, SliceOrder, OrderIntent, ExecutionReport
)
from shared.schemas import current_cycle_id
from pydantic import ValidationError
from shared.hl.client import HLConfig, HyperliquidClient
from shared.hl.rate_limiter import RedisTokenBucket, TokenBucket
from shared.metrics.prom import start_metrics, MSG_IN, MSG_OUT, ERR, LAT, set_alarm

SERVICE = "execution"
os.environ["SERVICE_NAME"] = SERVICE

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
HL_HTTP_URL = os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz")
HL_ACCOUNT_ADDRESS = os.environ.get("HL_ACCOUNT_ADDRESS", "").strip()
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "").strip()

STREAM_IN = "risk.approved"
STREAM_CTL = "ctl.commands"
STREAM_STATE_KEY = "latest.state.snapshot"
STREAM_PLAN = "exec.plan"
STREAM_ORDERS = "exec.orders"
STREAM_REPORTS = "exec.reports"
AUDIT = "audit.logs"
CTL_MODE_KEY = "ctl.mode"

DLQ_IN = "dlq.risk.approved"

GROUP = "exec_grp"
GROUP_CTL = "exec_ctl_grp"
CONSUMER = os.environ.get("CONSUMER", "exec_1")

# execution params
SLICES = int(os.environ.get("TWAP_SLICES", "3"))
SLICE_INTERVAL_S = int(os.environ.get("SLICE_INTERVAL_S", "20"))
SLICE_TIMEOUT_S = int(os.environ.get("SLICE_TIMEOUT_S", "20"))
PX_GUARD_BPS = int(os.environ.get("PX_GUARD_BPS", "10"))

MAX_ORDERS_PER_MIN = int(os.environ.get("MAX_ORDERS_PER_MIN", "30"))
MAX_CANCELS_PER_MIN = int(os.environ.get("MAX_CANCELS_PER_MIN", "30"))
MIN_NOTIONAL_USD = float(os.environ.get("MIN_NOTIONAL_USD", "10"))
MIN_NOTIONAL_SAFETY_RATIO = float(os.environ.get("MIN_NOTIONAL_SAFETY_RATIO", "1.02"))
META_TTL_S = int(os.environ.get("META_TTL_S", "3600"))
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))

# def make_cycle_id(asof_minute: str) -> str:
#     # "2026-02-18T16:00:00Z" -> "20260218T1600Z"
#     s = asof_minute.replace("-", "").replace(":", "")
#     s = s.replace("00Z", "Z")
#     return s[:13] + "00Z"

def bps_to_frac(bps: int) -> float:
    return bps / 10000.0

_META_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}}

def _as_decimal(x: float) -> Decimal:
    return Decimal(str(x))

def round_size(qty: float, sz_decimals: int) -> float:
    if qty <= 0:
        return 0.0
    decs = max(0, int(sz_decimals))
    quant = Decimal("1").scaleb(-decs)
    q = _as_decimal(qty).quantize(quant, rounding=ROUND_DOWN)
    return float(q)

def format_price(price: float, sz_decimals: int, max_decimals: int = 6) -> Optional[float]:
    if price <= 0:
        return None
    # Integer prices are always allowed.
    if abs(price - round(price)) < 1e-12:
        return float(int(round(price)))

    d = _as_decimal(price)
    if d == 0:
        return 0.0

    # 5 significant figures, with decimal places capped by MAX_DECIMALS - szDecimals
    max_decimals_allowed = max(0, int(max_decimals) - int(sz_decimals))
    # Decimal.adjusted() == floor(log10(abs(d)))
    sig_decimals = max(0, 4 - int(d.copy_abs().adjusted()))
    decimals = min(max_decimals_allowed, sig_decimals)

    quant = Decimal("1").scaleb(-decimals)
    d = d.quantize(quant, rounding=ROUND_DOWN)
    return float(d)

def is_min_notional_ok(qty: float, price: float, min_notional: float = MIN_NOTIONAL_USD) -> bool:
    return (qty * price) >= min_notional


def effective_slices_for_notional(
    total_qty: float,
    price: float,
    requested_slices: int,
    min_notional: float = MIN_NOTIONAL_USD,
    safety_ratio: float = MIN_NOTIONAL_SAFETY_RATIO,
) -> int:
    if total_qty <= 0 or price <= 0 or requested_slices <= 0:
        return 0
    required = max(0.0, float(min_notional)) * max(1.0, float(safety_ratio))
    total_notional = float(total_qty) * float(price)
    if total_notional < required:
        return 0
    max_slices = int(total_notional // required)
    if max_slices <= 0:
        return 0
    return max(1, min(int(requested_slices), max_slices))

def _fetch_meta(http: httpx.Client) -> Dict[str, int]:
    r = http.post(f"{HL_HTTP_URL}/info", json={"type": "meta"})
    r.raise_for_status()
    meta = r.json() or {}
    universe = meta.get("universe", []) or []
    out: Dict[str, int] = {}
    for item in universe:
        name = item.get("name") or item.get("coin")
        sz = item.get("szDecimals")
        if name is not None and sz is not None:
            try:
                out[str(name)] = int(sz)
            except Exception:
                continue
    return out

def get_sz_decimals(http: httpx.Client, symbol: str) -> int:
    now = time.time()
    cache = _META_CACHE.get("data") or {}
    if (now - float(_META_CACHE.get("ts", 0.0))) > META_TTL_S or not cache:
        try:
            cache = _fetch_meta(http)
            _META_CACHE["data"] = cache
            _META_CACHE["ts"] = now
        except Exception:
            # Keep old cache if refresh failed
            cache = _META_CACHE.get("data") or {}
    return int(cache.get(symbol, 0))

def extract_order_status(payload: Dict[str, Any]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    if "status" in payload and isinstance(payload["status"], str):
        return payload["status"]
    order = payload.get("order")
    if isinstance(order, dict) and isinstance(order.get("status"), str):
        return order["status"]
    return None

# Official orderStatus states for terminal detection
_CANCELED = {
    "canceled",
    "marginCanceled",
    "vaultWithdrawalCanceled",
    "openInterestCapCanceled",
    "selfTradeCanceled",
    "reduceOnlyCanceled",
    "siblingFilledCanceled",
    "delistedCanceled",
    "liquidatedCanceled",
    "scheduledCancel",
}
_REJECTED = {
    "rejected",
    "tickRejected",
    "minTradeNtlRejected",
    "perpMarginRejected",
    "reduceOnlyRejected",
    "badAloPxRejected",
    "iocCancelRejected",
    "badTriggerPxRejected",
    "marketOrderNoLiquidityRejected",
    "positionIncreaseAtOpenInterestCapRejected",
    "positionFlipAtOpenInterestCapRejected",
    "tooAggressiveAtOpenInterestCapRejected",
    "openInterestIncreaseRejected",
    "insufficientSpotBalanceRejected",
    "oracleRejected",
    "perpMaxPositionRejected",
}
_FILLED = {"filled"}
_OPEN = {"open", "triggered"}
_VALID_CTL_CMDS = {"HALT", "RESUME", "REDUCE_ONLY"}

def classify_order_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    if status in _FILLED:
        return "FILLED"
    if status in _CANCELED:
        return "CANCELED"
    if status in _REJECTED:
        return "REJECTED"
    if status in _OPEN:
        return "OPEN"
    return "UNKNOWN"


def terminal_status_or_none(classified_status: Optional[str]) -> Optional[str]:
    if classified_status in {"FILLED", "CANCELED", "REJECTED"}:
        return classified_status
    return None


def timeout_terminal_decision(cancel_allowed: bool, cancel_error: Optional[str] = None) -> Tuple[str, str]:
    if not cancel_allowed:
        return "REJECTED", "timeout_cancel_rate_limited"
    if cancel_error:
        return "REJECTED", "timeout_cancel_error"
    return "CANCELED", "timeout_cancel"


def make_skip_report(client_order_id: str, symbol: str, reason: str, **raw: Any) -> ExecutionReport:
    payload: Dict[str, Any] = {"skip_reason": reason}
    payload.update(raw)
    return ExecutionReport(
        client_order_id=client_order_id,
        symbol=symbol,
        status="REJECTED",
        raw=payload,
    )

def parse_ctl_command(payload: Dict[str, Any]) -> Tuple[Optional[str], str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    cmd = None
    reason = ""
    if isinstance(data, dict):
        cmd = data.get("cmd")
        reason = str(data.get("reason", ""))
    elif isinstance(payload, dict):
        cmd = payload.get("cmd")
        reason = str(payload.get("reason", ""))
    if not isinstance(cmd, str):
        return None, reason
    cmd_u = cmd.upper().strip()
    if cmd_u not in _VALID_CTL_CMDS:
        return None, reason
    return cmd_u, reason

def mode_from_command(cmd: str) -> str:
    if cmd == "HALT":
        return "HALT"
    if cmd == "REDUCE_ONLY":
        return "REDUCE_ONLY"
    return "NORMAL"

def get_control_mode(bus: RedisStreams) -> str:
    raw = bus.get_json(CTL_MODE_KEY)
    if not raw:
        return "NORMAL"
    mode = str(raw.get("mode", "NORMAL")).upper()
    if mode not in {"NORMAL", "REDUCE_ONLY", "HALT"}:
        return "NORMAL"
    return mode

def merge_modes(risk_mode: str, control_mode: str) -> str:
    if control_mode == "HALT":
        return "HALT"
    if control_mode == "REDUCE_ONLY":
        return "REDUCE_ONLY" if risk_mode == "NORMAL" else risk_mode
    return risk_mode

def get_latest_state(bus: RedisStreams) -> Optional[StateSnapshot]:
    st_raw = bus.get_json(STREAM_STATE_KEY)
    if not st_raw or "data" not in st_raw:
        return None
    try:
        return StateSnapshot(**st_raw["data"])
    except Exception:
        return None

def compute_current_weights(st: StateSnapshot) -> Dict[str, float]:
    eq = max(st.equity_usd, 1e-6)
    w = {}
    for sym in UNIVERSE:
        pos = st.positions.get(sym)
        if not pos or pos.mark_px <= 0:
            w[sym] = 0.0
        else:
            w[sym] = (pos.qty * pos.mark_px) / eq
    return w

def compute_target_weights(ap: ApprovedTargetPortfolio) -> Dict[str, float]:
    return {tw.symbol: tw.weight for tw in ap.approved_targets}

def plan_twap(ap: ApprovedTargetPortfolio, st: StateSnapshot, payload: Dict[str, Any]) -> ExecutionPlan:
    # cycle_id = make_cycle_id(ap.asof_minute)
    incoming_env = Envelope(**payload["env"])
    env = Envelope(source=SERVICE, cycle_id=incoming_env.cycle_id)

    cur_w = compute_current_weights(st)
    tgt_w = compute_target_weights(ap)
    eq = max(st.equity_usd, 1e-6)
    mid_px = {sym: st.positions[sym].mark_px for sym in UNIVERSE}

    # close-first order: reduce absolute exposure first
    deltas = []
    for sym in UNIVERSE:
        dw = tgt_w.get(sym, 0.0) - cur_w.get(sym, 0.0)
        deltas.append((sym, dw))

    def reduce_priority(item):
        sym, dw = item
        return abs(cur_w.get(sym, 0.0) + dw) - abs(cur_w.get(sym, 0.0))
    deltas.sort(key=reduce_priority)

    slices: List[SliceOrder] = []
    for sym, dw in deltas:
        px = mid_px.get(sym, 0.0)
        if px <= 0:
            continue

        if ap.mode == "REDUCE_ONLY":
            if abs(cur_w.get(sym, 0.0) + dw) > abs(cur_w.get(sym, 0.0)) + 1e-12:
                continue

        notional = dw * eq
        qty = notional / px
        if abs(qty) < 1e-6:
            continue

        side = "BUY" if qty > 0 else "SELL"
        total_qty = abs(qty)
        eff_slices = effective_slices_for_notional(
            total_qty=total_qty,
            price=px,
            requested_slices=max(SLICES, 1),
            min_notional=MIN_NOTIONAL_USD,
            safety_ratio=MIN_NOTIONAL_SAFETY_RATIO,
        )
        if eff_slices <= 0:
            continue
        per_slice_qty = total_qty / eff_slices

        # ignore too small
        if per_slice_qty * px < 1.0:
            continue

        for i in range(eff_slices):
            slices.append(SliceOrder(
                symbol=sym,
                side=side,
                qty=per_slice_qty,
                order_type="LIMIT",
                px_guard_bps=PX_GUARD_BPS,
                timeout_s=SLICE_TIMEOUT_S,
                slice_idx=i
            ))

    return ExecutionPlan(
        cycle_id=env.cycle_id,
        slices=slices,
        limits=ExecutionLimits(max_orders_per_min=MAX_ORDERS_PER_MIN, max_cancels_per_min=MAX_CANCELS_PER_MIN),
        idempotency_key=f"{env.cycle_id}:TWAP:{SLICES}"
    )

def order_status(client: httpx.Client, base_url: str, user: str, oid: int) -> Dict[str, Any]:
    # /info orderStatus reference: type=orderStatus, user, oid 
    r = client.post(f"{base_url}/info", json={"type": "orderStatus", "user": user, "oid": oid})
    r.raise_for_status()
    return r.json()

def main():
    start_metrics("METRICS_PORT", 9105)

    bus = RedisStreams(REDIS_URL)
    limiter = RedisTokenBucket(REDIS_URL)
    order_bucket = TokenBucket(key="rl:orders", rate_per_sec=MAX_ORDERS_PER_MIN/60.0, burst=MAX_ORDERS_PER_MIN)
    cancel_bucket = TokenBucket(key="rl:cancels", rate_per_sec=MAX_CANCELS_PER_MIN/60.0, burst=MAX_CANCELS_PER_MIN)
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

    def consume_ctl_commands() -> str:
        msgs = bus.xreadgroup_json(STREAM_CTL, GROUP_CTL, CONSUMER, count=20, block_ms=1)
        for stream, msg_id, payload in msgs:
            MSG_IN.labels(SERVICE, stream).inc()
            try:
                payload = require_env(payload)
                env = Envelope(**payload["env"])
                cmd, reason = parse_ctl_command(payload)
                if cmd is None:
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": Envelope(source=SERVICE, cycle_id=env.cycle_id).model_dump(),
                                "event": "ctl.invalid",
                                "data": payload.get("data"),
                            }
                        ),
                    )
                    bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
                    continue
                mode = mode_from_command(cmd)
                bus.set_json(
                    CTL_MODE_KEY,
                    {
                        "mode": mode,
                        "cmd": cmd,
                        "reason": reason,
                        "ts": env.ts,
                        "source": SERVICE,
                    },
                    ex=7 * 24 * 3600,
                )
                bus.xadd_json(
                    AUDIT,
                    require_env(
                        {
                            "env": Envelope(source=SERVICE, cycle_id=env.cycle_id).model_dump(),
                            "event": "ctl.applied",
                            "data": {"cmd": cmd, "mode": mode, "reason": reason},
                        }
                    ),
                )
                MSG_OUT.labels(SERVICE, AUDIT).inc()
                bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
            except Exception as e:
                env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "ctl.error", "err": str(e), "raw": payload}))
                note_error(env, "ctl", e)
        return get_control_mode(bus)

    hl = None
    if not DRY_RUN:
        if not (HL_ACCOUNT_ADDRESS and HL_PRIVATE_KEY):
            raise RuntimeError("DRY_RUN=false but HL_ACCOUNT_ADDRESS / HL_PRIVATE_KEY not set.")
        hl = HyperliquidClient(HLConfig(private_key=HL_PRIVATE_KEY, account_address=HL_ACCOUNT_ADDRESS, base_url=HL_HTTP_URL))

    with httpx.Client(timeout=6.0) as http:
        while True:
            control_mode = consume_ctl_commands()
            msgs = bus.xreadgroup_json(STREAM_IN, GROUP, CONSUMER, count=3, block_ms=5000)
            for stream, msg_id, payload in msgs:
                MSG_IN.labels(SERVICE, stream).inc()
                t0 = time.time()
                try:
                    try:
                        payload = require_env(payload)
                        incoming_env = Envelope(**payload["env"])
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
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        note_error(env, "protocol", e)
                        continue

                    try:
                        ap = ApprovedTargetPortfolio(**payload["data"])
                    except ValidationError as e:
                        env = Envelope(source=SERVICE, cycle_id=incoming_env.cycle_id)
                        dlq = {
                            "env": env.model_dump(),
                            "event": "schema_error",
                            "schema": "ApprovedTargetPortfolio",
                            "reason": str(e),
                            "data": payload.get("data"),
                        }
                        bus.xadd_json(f"dlq.{stream}", dlq)
                        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "schema_error", "schema": "ApprovedTargetPortfolio", "reason": str(e), "data": payload.get("data")}))
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        note_error(env, "schema", e)
                        continue
                    st = get_latest_state(bus)

                    if not st:
                        env = Envelope(source=SERVICE, cycle_id=incoming_env.cycle_id)
                        retry_or_dlq(bus, STREAM_IN, payload, env.model_dump(), RETRY, reason="missing_state")
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        note_error(env, "missing_state", RuntimeError("missing_state"))
                        continue

                    # cycle_id = make_cycle_id(ap.asof_minute)
                    
                    env = Envelope(source=SERVICE, cycle_id=incoming_env.cycle_id)

                    def emit_report(rep: ExecutionReport) -> None:
                        bus.xadd_json(STREAM_REPORTS, require_env({"env": env.model_dump(), "data": rep.model_dump()}))
                        MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()

                    effective_mode = merge_modes(ap.mode, control_mode)
                    if effective_mode != ap.mode:
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "mode.override",
                                    "data": {"risk_mode": ap.mode, "control_mode": control_mode, "effective_mode": effective_mode},
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()

                    if ap.decision_action in {"HOLD", "PROTECTIVE_ONLY"}:
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "exec.skip_decision_action",
                                    "data": {"decision_action": ap.decision_action, "mode": effective_mode},
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        continue

                    if effective_mode == "HALT":
                        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "exec.halt"}))
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        continue

                    ap_exec = ap.model_copy(update={"mode": effective_mode})
                    plan = plan_twap(ap_exec, st, payload)
                    bus.xadd_json(STREAM_PLAN, require_env({"env": env.model_dump(), "data": plan.model_dump()}))
                    MSG_OUT.labels(SERVICE, STREAM_PLAN).inc()

                    # Execute slices with timeout/cancel/replace
                    for s in plan.slices:
                        mid = st.positions[s.symbol].mark_px
                        if mid <= 0:
                            continue
                        guard = bps_to_frac(s.px_guard_bps)
                        if s.side == "BUY":
                            limit_px = mid * (1.0 + guard)
                            is_buy = True
                        else:
                            limit_px = mid * (1.0 - guard)
                            is_buy = False

                        client_order_id = f"{env.cycle_id}:{s.symbol}:{s.slice_idx}:{s.side}"
                        dedup_key = f"dedup:{client_order_id}"

                        sz_decimals = get_sz_decimals(http, s.symbol)
                        limit_px = format_price(limit_px, sz_decimals)
                        if limit_px is None or limit_px <= 0:
                            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "invalid_price", "symbol": s.symbol, "raw_px": mid}))
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env({"env": env.model_dump(), "data": make_skip_report(client_order_id, s.symbol, "invalid_price", raw_px=mid).model_dump()}),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                            continue

                        qty = round_size(s.qty, sz_decimals)
                        if qty <= 0:
                            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "invalid_size", "symbol": s.symbol, "raw_qty": s.qty}))
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env({"env": env.model_dump(), "data": make_skip_report(client_order_id, s.symbol, "invalid_size", raw_qty=s.qty).model_dump()}),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                            continue

                        if not is_min_notional_ok(qty, float(limit_px), MIN_NOTIONAL_USD):
                            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "min_notional_skip", "symbol": s.symbol, "qty": qty, "px": float(limit_px), "min_notional": MIN_NOTIONAL_USD}))
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "data": make_skip_report(
                                            client_order_id,
                                            s.symbol,
                                            "min_notional_skip",
                                            qty=qty,
                                            px=float(limit_px),
                                            min_notional=MIN_NOTIONAL_USD,
                                        ).model_dump(),
                                    }
                                ),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                            continue

                        dedup = bus.get_json(dedup_key)
                        if dedup and dedup.get("status") in ("ACK", "PARTIAL", "FILLED"):
                            # already submitted
                            continue

                        intent = OrderIntent(
                            action="PLACE",
                            symbol=s.symbol,
                            side=s.side,
                            qty=qty,
                            order_type="LIMIT",
                            limit_px=float(limit_px),
                            tif="Gtc",
                            client_order_id=client_order_id,
                            slice_ref=f"{env.cycle_id}:{s.symbol}:{s.slice_idx}",
                            reason="twap_rebalance"
                        )
                        
                        bus.xadd_json(STREAM_ORDERS, require_env({"env": env.model_dump(), "data": intent.model_dump()}))
                        MSG_OUT.labels(SERVICE, STREAM_ORDERS).inc()

                        if not limiter.allow(order_bucket, cost=1):
                            # rate-limited: skip this slice
                            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "rate_limited", "what": "order"}))
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env({"env": env.model_dump(), "data": make_skip_report(client_order_id, s.symbol, "rate_limited").model_dump()}),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                            continue

                        t_submit = time.time()
                        if DRY_RUN:
                            rep = ExecutionReport(
                                client_order_id=client_order_id,
                                exchange_order_id=None,
                                symbol=s.symbol,
                                status="ACK",
                                raw={"dry_run": True},
                                latency_ms=int((time.time() - t_submit) * 1000),
                            )
                            bus.set_json(dedup_key, {"status": rep.status, "exchange_order_id": None}, ex=3600)
                            emit_report(rep)
                            time.sleep(SLICE_INTERVAL_S)
                            continue

                        # real submit
                        try:
                            assert hl is not None
                            res = hl.place_limit(
                                coin=s.symbol,
                                is_buy=is_buy,
                                sz=float(qty),
                                limit_px=float(limit_px),
                                tif="Gtc",
                                reduce_only=(effective_mode == "REDUCE_ONLY")
                            )
                            oid = None
                            if isinstance(res, dict):
                                oid = res.get("oid") or res.get("orderId")
                            rep = ExecutionReport(
                                client_order_id=client_order_id,
                                exchange_order_id=str(oid) if oid else None,
                                symbol=s.symbol,
                                status="ACK",
                                raw=res if isinstance(res, dict) else {"res": str(res)},
                                latency_ms=int((time.time() - t_submit) * 1000),
                            )
                            bus.set_json(dedup_key, {"status": rep.status, "exchange_order_id": rep.exchange_order_id}, ex=3600)
                            emit_report(rep)
                        except Exception as e:
                            ERR.labels(SERVICE, "submit").inc()
                            rep = ExecutionReport(
                                client_order_id=client_order_id,
                                symbol=s.symbol,
                                status="REJECTED",
                                raw={"error": str(e)},
                                latency_ms=int((time.time() - t_submit) * 1000),
                            )
                            emit_report(rep)
                            continue

                        # Track status until timeout, then force terminal report.
                        oid_int = None
                        try:
                            if rep.exchange_order_id:
                                oid_int = int(rep.exchange_order_id)
                        except Exception:
                            oid_int = None

                        terminal_emitted = False
                        if oid_int is None:
                            missing_oid = ExecutionReport(
                                client_order_id=client_order_id,
                                exchange_order_id=rep.exchange_order_id,
                                symbol=s.symbol,
                                status="REJECTED",
                                raw={"reason": "missing_exchange_order_id_after_ack"},
                            )
                            emit_report(missing_oid)
                            bus.set_json(dedup_key, {"status": "REJECTED", "exchange_order_id": rep.exchange_order_id}, ex=3600)
                            terminal_emitted = True
                        else:
                            deadline = time.time() + s.timeout_s
                            last = None
                            while time.time() < deadline:
                                try:
                                    stj = order_status(http, HL_HTTP_URL, HL_ACCOUNT_ADDRESS, oid_int)
                                    last = stj
                                    status = extract_order_status(stj)
                                    cls = terminal_status_or_none(classify_order_status(status))
                                    if cls is not None:
                                        terminal = ExecutionReport(
                                            client_order_id=client_order_id,
                                            exchange_order_id=rep.exchange_order_id,
                                            symbol=s.symbol,
                                            status=cls,
                                            raw=stj,
                                        )
                                        emit_report(terminal)
                                        bus.set_json(dedup_key, {"status": cls, "exchange_order_id": rep.exchange_order_id}, ex=3600)
                                        terminal_emitted = True
                                        break
                                    time.sleep(1.0)
                                except Exception:
                                    time.sleep(1.0)

                            if not terminal_emitted:
                                cancel_allowed = limiter.allow(cancel_bucket, cost=1)
                                cancel_error = None
                                if cancel_allowed:
                                    try:
                                        hl.cancel(s.symbol, oid_int)
                                    except Exception as e:
                                        cancel_error = str(e)
                                        ERR.labels(SERVICE, "cancel").inc()
                                        bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "cancel_error", "err": cancel_error}))
                                else:
                                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "rate_limited", "what": "cancel"}))

                                terminal_status, terminal_reason = timeout_terminal_decision(cancel_allowed, cancel_error)
                                timeout_rep = ExecutionReport(
                                    client_order_id=client_order_id,
                                    exchange_order_id=rep.exchange_order_id,
                                    symbol=s.symbol,
                                    status=terminal_status,
                                    raw={
                                        terminal_reason: True,
                                        "cancel_allowed": cancel_allowed,
                                        "cancel_error": cancel_error,
                                        "last_status": last,
                                    },
                                )
                                emit_report(timeout_rep)
                                bus.set_json(dedup_key, {"status": terminal_status, "exchange_order_id": rep.exchange_order_id}, ex=3600)
                                terminal_emitted = True

                        time.sleep(SLICE_INTERVAL_S)

                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "exec.done", "data": {"cycle_id": env.cycle_id, "n_slices": len(plan.slices)}}))
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    note_ok(env)
                    LAT.labels(SERVICE, "cycle").observe(time.time() - t0)

                except Exception as e:
                    env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                    retry_or_dlq(bus, STREAM_IN, payload, env.model_dump(), RETRY, reason=str(e))
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    note_error(env, "handler", e)

if __name__ == "__main__":
    main()
