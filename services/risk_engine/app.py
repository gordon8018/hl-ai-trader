import os
import time
import json
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict, Any
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
CTL_MODE_KEY = "ctl.mode"

GROUP = "risk_grp"
CONSUMER = os.environ.get("CONSUMER", "risk_1")
RETRY = RetryPolicy(max_retries=int(os.environ.get("MAX_RETRIES", "5")))
STATE_STALE_SECS = int(os.environ.get("STATE_STALE_SECS", "45"))
DAILY_LOSS_REDUCE_ONLY_PCT = float(os.environ.get("DAILY_LOSS_REDUCE_ONLY_PCT", "0.03"))
DAILY_LOSS_HALT_PCT = float(os.environ.get("DAILY_LOSS_HALT_PCT", "0.05"))
CONSECUTIVE_REJECTED_REDUCE_ONLY = int(os.environ.get("CONSECUTIVE_REJECTED_REDUCE_ONLY", "8"))
CONSECUTIVE_REJECTED_HALT = int(os.environ.get("CONSECUTIVE_REJECTED_HALT", "20"))
EXEC_REPORT_SCAN_LIMIT = int(os.environ.get("EXEC_REPORT_SCAN_LIMIT", "200"))

SERVICE = "risk_engine"
os.environ["SERVICE_NAME"] = SERVICE

def cap_for(sym: str) -> float:
    if sym in ("BTC", "ETH"):
        return float(os.environ.get("CAP_BTC_ETH", "0.15"))
    return float(os.environ.get("CAP_ALT", "0.10"))

def get_control_mode(bus: RedisStreams) -> str:
    raw = bus.get_json(CTL_MODE_KEY)
    if not raw:
        return "NORMAL"
    mode = str(raw.get("mode", "NORMAL")).upper()
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

def health_mode(state_env: dict, st: Optional[StateSnapshot], stale_secs: int) -> Tuple[str, List[Rejection]]:
    if st is None:
        return "HALT", [Rejection(symbol="*", reason="state_missing", original_weight=0.0, approved_weight=0.0)]

    rejections: List[Rejection] = []
    health = st.health if isinstance(st.health, dict) else {}
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


def daily_equity_baseline(bus: RedisStreams, current_equity: float, now: datetime) -> float:
    day = now.strftime("%Y%m%d")
    key = f"risk.daily_baseline.{day}"
    cur = max(float(current_equity), 1e-9)
    got = bus.get_json(key)
    if got and isinstance(got, dict):
        val = got.get("equity_usd")
        if isinstance(val, (int, float)) and float(val) > 0:
            return float(val)
    bus.set_json(key, {"equity_usd": cur, "ts": now.replace(microsecond=0).isoformat().replace("+00:00", "Z")}, ex=3 * 24 * 3600)
    return cur

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

                mode, mode_rejections = health_mode(state_env, st, STATE_STALE_SECS)

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

                # Hard risk protection 2: consecutive execution rejects guard.
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

                mode = escalate_mode(mode, daily_loss_mode, reject_mode)
                control_mode = get_control_mode(bus)
                mode = merge_modes(mode, control_mode)

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
                # If we are not in NORMAL, keep only reducing weights and avoid increases.
                if mode in {"HALT", "REDUCE_ONLY"} and st:
                    cur = {}
                    eq = max(st.equity_usd, 1e-6)
                    for sym in UNIVERSE:
                        pos = st.positions.get(sym)
                        cur[sym] = 0.0 if not pos else (pos.qty * pos.mark_px) / eq
                    constrained = []
                    for tw in approved:
                        cw = cur.get(tw.symbol, 0.0)
                        if mode == "HALT":
                            constrained.append(TargetWeight(symbol=tw.symbol, weight=cw))
                            continue
                        # REDUCE_ONLY: target cannot increase absolute exposure
                        if abs(tw.weight) > abs(cw) + 1e-12:
                            constrained.append(TargetWeight(symbol=tw.symbol, weight=cw))
                            rejections.append(Rejection(symbol=tw.symbol, reason="reduce_only_block_increase", original_weight=tw.weight, approved_weight=cw))
                        else:
                            constrained.append(tw)
                    approved = constrained

                rejections.extend(mode_rejections)

                ap = ApprovedTargetPortfolio(
                    asof_minute=tp.asof_minute,
                    mode=mode,
                    decision_action=tp.decision_action,
                    approved_targets=approved,
                    rejections=rejections,
                    risk_summary={"gross": sum(abs(x.weight) for x in approved),
                                  "net": sum(x.weight for x in approved),
                                  "control_mode": control_mode,
                                  "daily_loss_ratio": daily_loss_ratio,
                                  "consecutive_rejected": reject_streak},
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
