# services/execution/app.py
import os
import time
import math
import json
import threading
import logging
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, Optional, List, Tuple, Literal, cast

from shared.bus.redis_streams import RedisStreams
from shared.bus.guard import require_env
from shared.bus.dlq import RetryPolicy, retry_or_dlq
from shared.schemas import (
    Envelope,
    ApprovedTargetPortfolio,
    ExecutionLimits,
    StateSnapshot,
    ExecutionPlan,
    SliceOrder,
    OrderIntent,
    ExecutionReport,
)
from shared.schemas import current_cycle_id
from pydantic import ValidationError
from shared.exchange.factory import create_exchange_adapter
from shared.exchange.base import ExchangeError, OrderRejectedError
from shared.hl.rate_limiter import RedisTokenBucket, TokenBucket
from shared.metrics.prom import (
    start_metrics,
    MSG_IN,
    MSG_OUT,
    ERR,
    LAT,
    set_alarm,
    EXEC_ORDERS,
    EXEC_REJECT,
    EXEC_FILL_LAT_MS,
    EXEC_RATE_LIMIT,
    EXEC_CTL_PENDING_COUNT,
)

SERVICE = "execution"
os.environ["SERVICE_NAME"] = SERVICE
logger = logging.getLogger(__name__)

REDIS_URL = os.environ["REDIS_URL"]
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

STREAM_IN = "risk.approved"
STREAM_CTL = "ctl.commands"
STREAM_STATE_KEY = "latest.state.snapshot"
STREAM_PLAN = "exec.plan"
STREAM_ORDERS = "exec.orders"
STREAM_REPORTS = "exec.reports"
AUDIT = "audit.logs"
CTL_MODE_KEY = "ctl.mode"

# Order tracking queue (for async tracking)
ORDER_TRACKING_QUEUE = "exec.order_tracking_queue"

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
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))

# def make_cycle_id(asof_minute: str) -> str:
#     # "2026-02-18T16:00:00Z" -> "20260218T1600Z"
#     s = asof_minute.replace("-", "").replace(":", "")
#     s = s.replace("00Z", "Z")
#     return s[:13] + "00Z"


def bps_to_frac(bps: int) -> float:
    return bps / 10000.0


def _as_decimal(x: float) -> Decimal:
    return Decimal(str(x))


def _qty_decimals_from_step(qty_step: float) -> int:
    """Convert qty_step to number of decimal places.

    Examples: 0.001 -> 3, 0.01 -> 2, 1.0 -> 0
    """
    if qty_step <= 0 or qty_step >= 1.0:
        return 0
    return max(0, round(-math.log10(qty_step)))


def round_size(qty: float, sz_decimals: int) -> float:
    if qty <= 0:
        return 0.0
    decs = max(0, int(sz_decimals))
    quant = Decimal("1").scaleb(-decs)
    q = _as_decimal(qty).quantize(quant, rounding=ROUND_DOWN)
    return float(q)


def format_price(
    price: float, sz_decimals: int, max_decimals: int = 6
) -> Optional[float]:
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


def is_min_notional_ok(
    qty: float, price: float, min_notional: float = MIN_NOTIONAL_USD
) -> bool:
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



_VALID_CTL_CMDS = {"HALT", "RESUME", "REDUCE_ONLY"}


def timeout_terminal_decision(
    cancel_allowed: bool, cancel_error: Optional[str] = None
) -> Tuple[str, str]:
    if not cancel_allowed:
        return "REJECTED", "timeout_cancel_rate_limited"
    if cancel_error:
        return "REJECTED", "timeout_cancel_error"
    return "CANCELED", "timeout_cancel"


def make_skip_report(
    client_order_id: str, symbol: str, reason: str, **raw: Any
) -> ExecutionReport:
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


def normalize_ctl_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload_type")

    raw = payload.get("p")
    if raw is None:
        return payload

    if not isinstance(raw, str):
        raise ValueError("invalid_wrapped_payload")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid_wrapped_payload: {e}") from e

    if not isinstance(parsed, dict):
        raise ValueError("invalid_wrapped_payload")
    return parsed


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


def should_ack_ctl_apply_error(dlq_written: bool) -> bool:
    # Prefer system liveness: avoid infinite redelivery storms in ctl consumer.
    # DLQ write is best-effort; caller always ACKs after handling.
    return True


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


def emit_ctl_pending_count(bus: RedisStreams) -> None:
    try:
        pending = 0
        summary = bus.r.xpending(STREAM_CTL, GROUP_CTL)
        if isinstance(summary, dict):
            pending = int(summary.get("pending", 0) or 0)
        elif isinstance(summary, (list, tuple)) and summary:
            pending = int(summary[0] or 0)
        EXEC_CTL_PENDING_COUNT.labels(SERVICE).set(max(pending, 0))
    except Exception:
        # Best-effort metric only: never let control-loop telemetry break liveness.
        return


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


def plan_twap(
    ap: ApprovedTargetPortfolio, st: StateSnapshot, payload: Dict[str, Any]
) -> ExecutionPlan:
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
            slices.append(
                SliceOrder(
                    symbol=sym,
                    side=side,
                    qty=per_slice_qty,
                    order_type="LIMIT",
                    px_guard_bps=PX_GUARD_BPS,
                    timeout_s=SLICE_TIMEOUT_S,
                    slice_idx=i,
                )
            )

    return ExecutionPlan(
        cycle_id=env.cycle_id,
        slices=slices,
        limits=ExecutionLimits(
            max_orders_per_min=MAX_ORDERS_PER_MIN,
            max_cancels_per_min=MAX_CANCELS_PER_MIN,
        ),
        idempotency_key=f"{env.cycle_id}:TWAP:{SLICES}",
    )


class AsyncOrderTracker:
    """
    Asynchronous order tracker that runs in a separate thread.
    Tracks submitted orders and emits terminal reports when they complete.
    """

    def __init__(
        self,
        bus: RedisStreams,
        exchange,
        limiter: RedisTokenBucket,
        cancel_bucket: TokenBucket,
        status_bucket: TokenBucket,
    ):
        self.bus = bus
        self.exchange = exchange
        self.limiter = limiter
        self.cancel_bucket = cancel_bucket
        self.status_bucket = status_bucket
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background tracking thread."""
        self._running = True
        self._thread = threading.Thread(target=self._tracking_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the tracking thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def add_order_to_track(
        self,
        client_order_id: str,
        exchange_order_id: int,
        symbol: str,
        side: str,
        timeout_s: int,
        cycle_id: str,
        slice_idx: int,
        dedup_key: str,
    ) -> None:
        """Add an order to the tracking queue."""
        tracking_data = {
            "client_order_id": client_order_id,
            "exchange_order_id": exchange_order_id,
            "symbol": symbol,
            "side": side,
            "timeout_s": timeout_s,
            "cycle_id": cycle_id,
            "slice_idx": slice_idx,
            "dedup_key": dedup_key,
            "added_ts": time.time(),
            "deadline_ts": time.time() + timeout_s,
            "last_poll_ts": 0.0,
        }
        self.bus.r.rpush(ORDER_TRACKING_QUEUE, json.dumps(tracking_data))

    def _tracking_loop(self) -> None:
        """Background loop that polls order statuses."""
        while self._running:
            try:
                self._process_tracking_queue()
            except Exception as e:
                ERR.labels(SERVICE, "order_tracker_loop").inc()
            time.sleep(1.0)

    def _process_tracking_queue(self) -> None:
        """Process orders in the tracking queue."""
        pending = cast(List[str], self.bus.r.lrange(ORDER_TRACKING_QUEUE, 0, -1))
        if not pending:
            return

        to_remove: List[str] = []
        now = time.time()
        for idx, item in enumerate(pending):
            try:
                order = json.loads(item) if isinstance(item, str) else item
            except (json.JSONDecodeError, TypeError):
                if isinstance(item, str):
                    to_remove.append(item)
                continue

            oid = order.get("exchange_order_id")
            if oid is None:
                if isinstance(item, str):
                    to_remove.append(item)
                continue
            client_order_id = order.get("client_order_id")
            symbol = order.get("symbol")
            cycle_id = order.get("cycle_id", "")
            deadline_ts = order.get("deadline_ts", 0)
            dedup_key = order.get("dedup_key", f"dedup:{client_order_id}")
            last_poll_ts = order.get("last_poll_ts", 0.0)

            # Check timeout
            if now >= deadline_ts:
                cancel_allowed = self.limiter.allow(self.cancel_bucket, cost=1)
                cancel_error = None
                if cancel_allowed and self.exchange is not None:
                    try:
                        self.exchange.cancel_order(symbol, str(oid))
                    except Exception as e:
                        cancel_error = str(e)
                        ERR.labels(SERVICE, "cancel").inc()
                        self.bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": Envelope(
                                        source=SERVICE, cycle_id=cycle_id
                                    ).model_dump(),
                                    "event": "cancel_error",
                                    "err": cancel_error,
                                }
                            ),
                        )
                elif not cancel_allowed:
                    self.bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": Envelope(
                                    source=SERVICE, cycle_id=cycle_id
                                ).model_dump(),
                                "event": "rate_limited",
                                "what": "cancel",
                            }
                        ),
                    )
                    EXEC_RATE_LIMIT.labels(SERVICE, "cancel").inc()

                terminal_status, terminal_reason = timeout_terminal_decision(
                    cancel_allowed, cancel_error
                )
                self._emit_terminal_report(
                    client_order_id=client_order_id,
                    exchange_order_id=str(oid),
                    symbol=symbol,
                    status=cast(
                        Literal["ACK", "PARTIAL", "FILLED", "CANCELED", "REJECTED"],
                        terminal_status,
                    ),
                    raw={
                        "timeout": True,
                        terminal_reason: True,
                        "cancel_allowed": cancel_allowed,
                        "cancel_error": cancel_error,
                    },
                    cycle_id=cycle_id,
                    dedup_key=dedup_key,
                )
                if isinstance(item, str):
                    to_remove.append(item)
                continue

            # Poll order status
            if (now - float(last_poll_ts)) < 1.0:
                continue

            if not self.limiter.allow(self.status_bucket, cost=1):
                EXEC_RATE_LIMIT.labels(SERVICE, "order_status").inc()
                continue

            try:
                order_result = self.exchange.get_order_status(symbol, str(oid))
                # Map normalized status to internal uppercase status
                status_map = {
                    "filled": "FILLED",
                    "canceled": "CANCELED",
                    "rejected": "REJECTED",
                    "ack": "ACK",
                }
                status_cls = status_map.get(order_result.status, "ACK")

                if status_cls in ("FILLED", "CANCELED", "REJECTED"):
                    self._emit_terminal_report(
                        client_order_id=client_order_id,
                        exchange_order_id=str(oid),
                        symbol=symbol,
                        status=cast(
                            Literal["ACK", "PARTIAL", "FILLED", "CANCELED", "REJECTED"],
                            status_cls,
                        ),
                        raw={
                            "status": order_result.status,
                            "filled_qty": order_result.filled_qty,
                            "avg_px": order_result.avg_px,
                        },
                        cycle_id=cycle_id,
                        dedup_key=dedup_key,
                    )
                    if isinstance(item, str):
                        to_remove.append(item)
            except Exception:
                pass  # Continue tracking, will retry
            finally:
                if isinstance(order, dict):
                    order["last_poll_ts"] = now
                    self.bus.r.lset(ORDER_TRACKING_QUEUE, idx, json.dumps(order))

        # Remove processed orders from queue
        for value in to_remove:
            self.bus.r.lrem(ORDER_TRACKING_QUEUE, 1, value)

    def _emit_terminal_report(
        self,
        client_order_id: str,
        exchange_order_id: Optional[str],
        symbol: str,
        status: Literal["ACK", "PARTIAL", "FILLED", "CANCELED", "REJECTED"],
        raw: Dict[str, Any],
        cycle_id: str,
        dedup_key: str,
    ) -> None:
        """Emit terminal status report and update dedup cache."""
        env = Envelope(source=SERVICE, cycle_id=cycle_id)
        report = ExecutionReport(
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            symbol=symbol,
            status=status,
            raw=raw,
        )
        self.bus.xadd_json(
            STREAM_REPORTS,
            require_env({"env": env.model_dump(), "data": report.model_dump()}),
        )
        self.bus.set_json(
            dedup_key,
            {"status": status, "exchange_order_id": exchange_order_id},
            ex=3600,
        )
        MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()


def main():
    start_metrics("METRICS_PORT", 9105)

    bus = RedisStreams(REDIS_URL)
    limiter = RedisTokenBucket(REDIS_URL)
    order_bucket = TokenBucket(
        key="rl:orders",
        rate_per_sec=MAX_ORDERS_PER_MIN / 60.0,
        burst=MAX_ORDERS_PER_MIN,
    )
    cancel_bucket = TokenBucket(
        key="rl:cancels",
        rate_per_sec=MAX_CANCELS_PER_MIN / 60.0,
        burst=MAX_CANCELS_PER_MIN,
    )
    error_streak = 0
    alarm_on = False

    # Initialize exchange adapter (always, even in DRY_RUN for instrument spec lookup)
    _exchange = create_exchange_adapter()

    # Initialize and start async order tracker
    status_bucket = TokenBucket(
        key="rl:order_status",
        rate_per_sec=MAX_ORDERS_PER_MIN / 60.0,
        burst=MAX_ORDERS_PER_MIN,
    )
    tracker = AsyncOrderTracker(
        bus=bus,
        exchange=_exchange,
        limiter=limiter,
        cancel_bucket=cancel_bucket,
        status_bucket=status_bucket,
    )
    if not DRY_RUN:
        tracker.start()

    def note_error(env: Envelope, where: str, err: Exception) -> None:
        nonlocal error_streak, alarm_on
        error_streak += 1
        ERR.labels(SERVICE, where).inc()
        if error_streak >= ERROR_STREAK_THRESHOLD and not alarm_on:
            alarm_on = True
            set_alarm("error_streak", True)
            bus.xadd_json(
                AUDIT,
                require_env(
                    {
                        "env": env.model_dump(),
                        "event": "alarm",
                        "where": where,
                        "reason": "error_streak",
                    }
                ),
            )

    def note_ok(env: Envelope) -> None:
        nonlocal error_streak, alarm_on
        if error_streak > 0:
            error_streak = 0
        if alarm_on:
            alarm_on = False
            set_alarm("error_streak", False)
            bus.xadd_json(
                AUDIT,
                require_env(
                    {
                        "env": env.model_dump(),
                        "event": "alarm_cleared",
                        "reason": "recovered",
                    }
                ),
            )

    def consume_ctl_commands() -> str:
        msgs = safe_xreadgroup_json(
            bus,
            STREAM_CTL,
            GROUP_CTL,
            CONSUMER,
            count=20,
            block_ms=1,
        )
        for stream, msg_id, payload in msgs:
            MSG_IN.labels(SERVICE, stream).inc()
            try:
                payload = normalize_ctl_payload(payload)
            except ValueError as e:
                env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                bus.xadd_json(
                    AUDIT,
                    require_env(
                        {
                            "env": env.model_dump(),
                            "event": "ctl.invalid_format",
                            "err": str(e),
                            "raw": payload,
                        }
                    ),
                )
                bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
                continue
            try:
                payload = require_env(payload)
                env = Envelope(**payload["env"])
                cmd, reason = parse_ctl_command(payload)
                if cmd is None:
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": Envelope(
                                    source=SERVICE, cycle_id=env.cycle_id
                                ).model_dump(),
                                "event": "ctl.invalid_command",
                                "data": payload.get("data"),
                            }
                        ),
                    )
                    # Backward-compatible audit event name for existing consumers.
                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": Envelope(
                                    source=SERVICE, cycle_id=env.cycle_id
                                ).model_dump(),
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
                            "env": Envelope(
                                source=SERVICE, cycle_id=env.cycle_id
                            ).model_dump(),
                            "event": "ctl.applied",
                            "data": {"cmd": cmd, "mode": mode, "reason": reason},
                        }
                    ),
                )
                MSG_OUT.labels(SERVICE, AUDIT).inc()
                bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
            except Exception as e:
                env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                dlq_written = False
                try:
                    try:
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "ctl.apply_error",
                                    "err": str(e),
                                    "raw": payload,
                                }
                            ),
                        )
                    except Exception:
                        pass
                    bus.xadd_json(
                        "dlq.ctl.commands",
                        {
                            "env": env.model_dump(),
                            "reason": str(e),
                            "data": payload,
                        },
                    )
                    dlq_written = True
                finally:
                    if should_ack_ctl_apply_error(dlq_written):
                        bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
                note_error(env, "ctl", e)
        emit_ctl_pending_count(bus)
        return get_control_mode(bus)

    try:
        while True:
            control_mode = consume_ctl_commands()
            msgs = safe_xreadgroup_json(
                bus,
                STREAM_IN,
                GROUP,
                CONSUMER,
                count=3,
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
                        env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                        dlq = {
                            "env": env.model_dump(),
                            "event": "protocol_error",
                            "reason": str(e),
                            "data": payload,
                        }
                        bus.xadd_json(f"dlq.{stream}", dlq)
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "protocol_error",
                                    "reason": str(e),
                                    "data": payload,
                                }
                            ),
                        )
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
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "schema_error",
                                    "schema": "ApprovedTargetPortfolio",
                                    "reason": str(e),
                                    "data": payload.get("data"),
                                }
                            ),
                        )
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        note_error(env, "schema", e)
                        continue
                    st = get_latest_state(bus)

                    if not st:
                        env = Envelope(source=SERVICE, cycle_id=incoming_env.cycle_id)
                        retry_or_dlq(
                            bus,
                            STREAM_IN,
                            payload,
                            env.model_dump(),
                            RETRY,
                            reason="missing_state",
                        )
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        note_error(env, "missing_state", RuntimeError("missing_state"))
                        continue

                    # cycle_id = make_cycle_id(ap.asof_minute)

                    env = Envelope(source=SERVICE, cycle_id=incoming_env.cycle_id)

                    def emit_report(rep: ExecutionReport) -> None:
                        bus.xadd_json(
                            STREAM_REPORTS,
                            require_env(
                                {"env": env.model_dump(), "data": rep.model_dump()}
                            ),
                        )
                        MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                        EXEC_ORDERS.labels(SERVICE, "report", rep.status).inc()
                        if rep.status == "REJECTED":
                            raw = rep.raw if isinstance(rep.raw, dict) else {}
                            reason = raw.get("reason") or raw.get("error") or "unknown"
                            EXEC_REJECT.labels(SERVICE, str(reason)).inc()
                        if rep.status in {"PARTIAL", "FILLED"} and rep.latency_ms:
                            EXEC_FILL_LAT_MS.labels(SERVICE).observe(
                                float(rep.latency_ms)
                            )

                    effective_mode = merge_modes(ap.mode, control_mode)
                    if effective_mode != ap.mode:
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "mode.override",
                                    "data": {
                                        "risk_mode": ap.mode,
                                        "control_mode": control_mode,
                                        "effective_mode": effective_mode,
                                    },
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
                                    "data": {
                                        "decision_action": ap.decision_action,
                                        "mode": effective_mode,
                                    },
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        continue

                    if effective_mode == "HALT":
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {"env": env.model_dump(), "event": "exec.halt"}
                            ),
                        )
                        bus.xack(STREAM_IN, GROUP, msg_id)
                        continue

                    ap_exec = ap.model_copy(update={"mode": effective_mode})
                    plan = plan_twap(ap_exec, st, payload)
                    bus.xadd_json(
                        STREAM_PLAN,
                        require_env(
                            {"env": env.model_dump(), "data": plan.model_dump()}
                        ),
                    )
                    MSG_OUT.labels(SERVICE, STREAM_PLAN).inc()

                    # Execute slices with timeout/cancel/replace
                    for s in plan.slices:
                        st = cast(StateSnapshot, st)
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

                        client_order_id = (
                            f"{env.cycle_id}:{s.symbol}:{s.slice_idx}:{s.side}"
                        )
                        dedup_key = f"dedup:{client_order_id}"

                        try:
                            spec = _exchange.get_instrument_spec(s.symbol)
                            sz_decimals = _qty_decimals_from_step(spec.qty_step)
                        except Exception:
                            sz_decimals = 0
                        limit_px = format_price(limit_px, sz_decimals)
                        if limit_px is None or limit_px <= 0:
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "event": "invalid_price",
                                        "symbol": s.symbol,
                                        "raw_px": mid,
                                    }
                                ),
                            )
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "data": make_skip_report(
                                            client_order_id,
                                            s.symbol,
                                            "invalid_price",
                                            raw_px=mid,
                                        ).model_dump(),
                                    }
                                ),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                            continue

                        qty = round_size(s.qty, sz_decimals)
                        if qty <= 0:
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "event": "invalid_size",
                                        "symbol": s.symbol,
                                        "raw_qty": s.qty,
                                    }
                                ),
                            )
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "data": make_skip_report(
                                            client_order_id,
                                            s.symbol,
                                            "invalid_size",
                                            raw_qty=s.qty,
                                        ).model_dump(),
                                    }
                                ),
                            )
                            MSG_OUT.labels(SERVICE, STREAM_REPORTS).inc()
                            continue

                        if not is_min_notional_ok(
                            qty, float(limit_px), MIN_NOTIONAL_USD
                        ):
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "event": "min_notional_skip",
                                        "symbol": s.symbol,
                                        "qty": qty,
                                        "px": float(limit_px),
                                        "min_notional": MIN_NOTIONAL_USD,
                                    }
                                ),
                            )
                            EXEC_REJECT.labels(SERVICE, "min_notional_skip").inc()
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
                        if dedup and dedup.get("status") in (
                            "ACK",
                            "PARTIAL",
                            "FILLED",
                        ):
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
                            reason="twap_rebalance",
                        )

                        bus.xadd_json(
                            STREAM_ORDERS,
                            require_env(
                                {"env": env.model_dump(), "data": intent.model_dump()}
                            ),
                        )
                        MSG_OUT.labels(SERVICE, STREAM_ORDERS).inc()
                        EXEC_ORDERS.labels(SERVICE, "PLACE", "intent").inc()

                        if not limiter.allow(order_bucket, cost=1):
                            # rate-limited: skip this slice
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "event": "rate_limited",
                                        "what": "order",
                                    }
                                ),
                            )
                            EXEC_RATE_LIMIT.labels(SERVICE, "order").inc()
                            bus.xadd_json(
                                STREAM_REPORTS,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "data": make_skip_report(
                                            client_order_id, s.symbol, "rate_limited"
                                        ).model_dump(),
                                    }
                                ),
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
                            bus.set_json(
                                dedup_key,
                                {"status": rep.status, "exchange_order_id": None},
                                ex=3600,
                            )
                            emit_report(rep)
                            time.sleep(SLICE_INTERVAL_S)
                            continue

                        # real submit
                        try:
                            assert _exchange is not None
                            res = _exchange.place_limit(
                                symbol=s.symbol,
                                is_buy=is_buy,
                                qty=float(qty),
                                limit_px=float(limit_px),
                                tif="GTC",
                                reduce_only=(effective_mode == "REDUCE_ONLY"),
                                client_order_id=client_order_id,
                            )
                            # Use normalized response fields directly
                            oid = res.exchange_order_id if res.exchange_order_id else None
                            rep = ExecutionReport(
                                client_order_id=client_order_id,
                                exchange_order_id=oid,
                                symbol=s.symbol,
                                status="ACK" if res.status == "ack" else res.status.upper(),
                                raw={
                                    "exchange_order_id": res.exchange_order_id,
                                    "status": res.status,
                                    "filled_qty": res.filled_qty,
                                    "avg_px": res.avg_px,
                                    "latency_ms": res.latency_ms,
                                },
                                latency_ms=int((time.time() - t_submit) * 1000),
                            )
                            bus.set_json(
                                dedup_key,
                                {
                                    "status": rep.status,
                                    "exchange_order_id": rep.exchange_order_id,
                                },
                                ex=3600,
                            )
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

                        # Add order to async tracking queue (non-blocking)
                        oid_int = None
                        try:
                            if rep.exchange_order_id:
                                oid_int = int(rep.exchange_order_id)
                        except Exception:
                            oid_int = None

                        if oid_int is None:
                            missing_oid = ExecutionReport(
                                client_order_id=client_order_id,
                                exchange_order_id=rep.exchange_order_id,
                                symbol=s.symbol,
                                status="REJECTED",
                                raw={"reason": "missing_exchange_order_id_after_ack"},
                            )
                            emit_report(missing_oid)
                            bus.set_json(
                                dedup_key,
                                {
                                    "status": "REJECTED",
                                    "exchange_order_id": rep.exchange_order_id,
                                },
                                ex=3600,
                            )
                        else:
                            # Add to async tracking queue instead of blocking
                            tracker.add_order_to_track(
                                client_order_id=client_order_id,
                                exchange_order_id=oid_int,
                                symbol=s.symbol,
                                side=s.side,
                                timeout_s=s.timeout_s,
                                cycle_id=env.cycle_id,
                                slice_idx=s.slice_idx,
                                dedup_key=dedup_key,
                            )

                        time.sleep(SLICE_INTERVAL_S)

                    bus.xadd_json(
                        AUDIT,
                        require_env(
                            {
                                "env": env.model_dump(),
                                "event": "exec.done",
                                "data": {
                                    "cycle_id": env.cycle_id,
                                    "n_slices": len(plan.slices),
                                },
                            }
                        ),
                    )
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    note_ok(env)
                    LAT.labels(SERVICE, "cycle").observe(time.time() - t0)

                except Exception as e:
                    env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                    retry_or_dlq(
                        bus, STREAM_IN, payload, env.model_dump(), RETRY, reason=str(e)
                    )
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    note_error(env, "handler", e)

    except KeyboardInterrupt:
        pass
    finally:
        # Cleanup: stop order tracker and close bus
        tracker.stop()
        bus.close()


if __name__ == "__main__":
    main()
