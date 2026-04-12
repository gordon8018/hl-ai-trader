#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple

import redis


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "docs" / "reports" / "hourly"
LATEST_PATH = REPORT_DIR / "latest.md"
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


@dataclass
class CmdResult:
    cmd: str
    code: int
    out: str
    err: str


def run_cmd(cmd: str) -> CmdResult:
    proc = subprocess.run(
        cmd,
        shell=True,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return CmdResult(cmd=cmd, code=proc.returncode, out=proc.stdout.strip(), err=proc.stderr.strip())


def read_current_version() -> dict:
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        raw = r.get("deployment.current_version")
    except Exception as exc:
        return {"error": f"redis_error: {exc}"}
    if not raw:
        return {"error": "deployment.current_version is empty"}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid_json: {exc}"}
    if not isinstance(obj, dict):
        return {"error": "payload_not_object"}
    return obj


def stream_ts(stream: str) -> Optional[str]:
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        rows = r.xrevrange(stream, count=1)
        if not rows:
            return None
        _, fields = rows[0]
        payload = json.loads(fields.get("p", "{}"))
        if not isinstance(payload, dict):
            return None
        env = payload.get("env", {})
        if isinstance(env, dict):
            return env.get("ts")
    except Exception:
        return None
    return None


def get_account_snapshot() -> dict:
    """Get latest account snapshot from state.snapshot stream."""
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        rows = r.xrevrange("state.snapshot", count=1)
        if not rows:
            return {"error": "no snapshot data"}
        _, fields = rows[0]
        payload = json.loads(fields.get("p", "{}"))
        if not isinstance(payload, dict):
            return {"error": "invalid payload"}
        data = payload.get("data", {})
        return data
    except Exception as exc:
        return {"error": f"redis_error: {exc}"}


def _spearman(x: List[float], y: List[float]) -> float:
    """Spearman rank correlation; returns NaN for n < 3."""
    n = len(x)
    if n < 3:
        return float("nan")

    def _rank(a: List[float]) -> List[float]:
        sorted_idx = sorted(range(n), key=lambda i: a[i])
        ranks = [0.0] * n
        for r, i in enumerate(sorted_idx):
            ranks[i] = r + 1.0
        return ranks

    rx, ry = _rank(x), _rank(y)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1.0 - 6.0 * d2 / (n * (n * n - 1))


def _factor_composite(snap: dict, sym: str) -> Optional[float]:
    """Compute the IC-weighted Layer-1 composite for one symbol from a 1h snapshot."""
    ret_1h = float(snap.get("ret_1h", {}).get(sym, 0.0))
    ret_4h = float(snap.get("ret_4h", {}).get(sym, 0.0))
    vol_1h = max(float(snap.get("vol_1h", {}).get(sym, 0.02)), 0.001)
    ret_24h_raw = snap.get("ret_24h", {}).get(sym)
    ret_24h = float(ret_24h_raw) if ret_24h_raw is not None else ret_4h * 6.0
    trend_agree = float(snap.get("trend_agree", {}).get(sym, 0.0))
    vol_regime = float(snap.get("vol_regime", {}).get(sym, 0.0))

    def _clip1(v: float) -> float:
        return max(-1.0, min(1.0, v))

    # Factor 1: mom_24h (IC=-0.047, fade)
    vol_24h_est = vol_1h * 4.899
    f_mom = -_clip1(ret_24h / max(vol_24h_est, 0.001))

    # Factor 2: atr_to_support (IC=+0.046)
    atr_raw = snap.get("atr_to_support", {}).get(sym)
    if atr_raw is not None:
        f_supp = _clip1((float(atr_raw) - 1.0) / 2.0)
    else:
        f_supp = trend_agree * max(0.0, 1.0 - vol_regime * 0.5)

    # Factor 3: macd_hist (IC=-0.015, fade)
    macd_raw = snap.get("macd_hist", {}).get(sym)
    if macd_raw is not None:
        mid_px = float(snap.get("mid_px", {}).get(sym, 1.0))
        f_macd = -_clip1(float(macd_raw) / max(vol_1h * mid_px, 1e-8))
    else:
        f_macd = -_clip1((ret_1h - ret_4h / 4.0) / vol_1h)

    w1, w2, w3 = 0.047, 0.046, 0.015
    return (w1 * f_mom + w2 * f_supp + w3 * f_macd) / (w1 + w2 + w3)


def compute_signal_ic(n_obs: int = 72) -> dict:
    """
    Rolling IC between Layer-1 composite signal and 1h/4h forward returns.
    Reads last n_obs entries from md.features.1h stream.
    Stores results back to Redis (TTL 7d) for external monitoring.
    Returns dict with keys: ic_1h, ic_4h, n_pairs, alert, detail.
    """
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        rows = r.xrevrange("md.features.1h", count=n_obs + 10)
    except Exception as exc:
        return {"error": str(exc), "ic_1h": None, "ic_4h": None}

    if len(rows) < 3:
        return {"ic_1h": None, "ic_4h": None, "n_pairs": 0, "alert": None}

    # Parse oldest→newest
    snapshots: list = []
    for _, fields in reversed(rows):
        try:
            payload = json.loads(fields.get("p", "{}"))
            data = payload.get("data", {})
            if data:
                snapshots.append(data)
        except Exception:
            continue

    if len(snapshots) < 3:
        return {"ic_1h": None, "ic_4h": None, "n_pairs": 0, "alert": None}

    universe: list = snapshots[0].get("universe", [])

    # 1h IC: signal[i] vs ret_1h[i+1]
    sig_1h, fwd_1h = [], []
    for i in range(len(snapshots) - 1):
        snap, nxt = snapshots[i], snapshots[i + 1]
        for sym in universe:
            c = _factor_composite(snap, sym)
            fwd = nxt.get("ret_1h", {}).get(sym)
            if c is not None and fwd is not None:
                sig_1h.append(c)
                fwd_1h.append(float(fwd))

    # 4h IC: signal[i] vs ret_4h[i+4]
    sig_4h, fwd_4h = [], []
    for i in range(len(snapshots) - 4):
        snap, fut = snapshots[i], snapshots[i + 4]
        for sym in universe:
            c = _factor_composite(snap, sym)
            fwd = fut.get("ret_4h", {}).get(sym)
            if c is not None and fwd is not None:
                sig_4h.append(c)
                fwd_4h.append(float(fwd))

    ic_1h = _spearman(sig_1h, fwd_1h) if len(sig_1h) >= 5 else None
    ic_4h = _spearman(sig_4h, fwd_4h) if len(sig_4h) >= 5 else None

    # L3: per-factor IC for dynamic weight adaptation
    def _factor_signals(snap_list: list, key: str, fwd_key: str, offset: int = 1) -> Tuple[List[float], List[float]]:
        """Extract single-factor signals and forward returns from snapshot list."""
        sigs, fwds = [], []
        for i in range(len(snap_list) - offset):
            snap, fut = snap_list[i], snap_list[i + offset]
            for sym in universe:
                fwd = fut.get(fwd_key, {}).get(sym)
                if fwd is None:
                    continue
                # factor value computation mirrors compute_quant_bias
                ret_1h = float(snap.get("ret_1h", {}).get(sym, 0.0))
                ret_4h = float(snap.get("ret_4h", {}).get(sym, 0.0))
                vol_1h = max(float(snap.get("vol_1h", {}).get(sym, 0.02)), 0.001)
                if key == "mom":
                    ret_24h_raw = snap.get("ret_24h", {}).get(sym)
                    ret_24h = float(ret_24h_raw) if ret_24h_raw is not None else ret_4h * 6.0
                    vol_24h = vol_1h * 4.899
                    v = -max(-1.0, min(1.0, ret_24h / max(vol_24h, 0.001)))
                elif key == "supp":
                    atr_raw = snap.get("atr_to_support", {}).get(sym)
                    if atr_raw is not None:
                        v = max(-1.0, min(1.0, (float(atr_raw) - 1.0) / 2.0))
                    else:
                        continue
                elif key == "macd":
                    macd_raw = snap.get("macd_hist", {}).get(sym)
                    if macd_raw is not None:
                        mid_px = float(snap.get("mid_px", {}).get(sym, 1.0))
                        v = -max(-1.0, min(1.0, float(macd_raw) / max(vol_1h * mid_px, 1e-8)))
                    else:
                        continue
                else:
                    continue
                sigs.append(v)
                fwds.append(float(fwd))
        return sigs, fwds

    sig_mom, fwd_mom = _factor_signals(snapshots, "mom", "ret_1h")
    sig_supp, fwd_supp = _factor_signals(snapshots, "supp", "ret_1h")
    sig_macd, fwd_macd = _factor_signals(snapshots, "macd", "ret_1h")

    ic_mom = _spearman(sig_mom, fwd_mom) if len(sig_mom) >= 5 else None
    ic_supp = _spearman(sig_supp, fwd_supp) if len(sig_supp) >= 5 else None
    ic_macd = _spearman(sig_macd, fwd_macd) if len(sig_macd) >= 5 else None

    alert: Optional[str] = None
    if ic_1h is not None and ic_4h is not None:
        if ic_1h < -0.03 and ic_4h < -0.03:
            alert = "WARN: IC_1h and IC_4h both negative — factor may have decayed"
        elif ic_1h < -0.05:
            alert = "WARN: IC_1h < -5% — consider re-research"

    # Persist latest IC to Redis so external monitors can read it
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        ic_record = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ic_1h": ic_1h,
            "ic_4h": ic_4h,
            "n_pairs_1h": len(sig_1h),
            "n_pairs_4h": len(sig_4h),
        }
        r.setex("ic.signal_ic_latest", 7 * 86400, json.dumps(ic_record))

        # L3: per-factor IC for dynamic weight adaptation in compute_quant_bias
        if ic_mom is not None or ic_supp is not None or ic_macd is not None:
            factor_ic_record = {
                "ts": ic_record["ts"],
                "ic_mom": ic_mom,
                "ic_supp": ic_supp,
                "ic_macd": ic_macd,
            }
            r.setex("ic.factor_ic_latest", 7 * 86400, json.dumps(factor_ic_record))
    except Exception:
        pass

    return {
        "ic_1h": ic_1h,
        "ic_4h": ic_4h,
        "n_pairs_1h": len(sig_1h),
        "n_pairs_4h": len(sig_4h),
        "alert": alert,
        "ic_mom": ic_mom,
        "ic_supp": ic_supp,
        "ic_macd": ic_macd,
    }


def get_risk_guard_status() -> dict:
    """Read M1 risk guard state from Redis for hourly report."""
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

        # Peak equity drawdown guard
        peak_raw = r.get("risk.peak_equity")
        peak_info: dict = json.loads(peak_raw) if peak_raw else {}

        # IC decay streak guard
        streak_raw = r.get("risk.ic_decay_streak")
        streak_info: dict = json.loads(streak_raw) if streak_raw else {}

        # Latest approved risk mode
        rows = r.xrevrange("risk.approved", count=1)
        mode = "UNKNOWN"
        daily_loss_usd: Optional[float] = None
        if rows:
            _, fields = rows[0]
            payload = json.loads(fields.get("p", "{}"))
            data = payload.get("data", {})
            mode = data.get("mode", "UNKNOWN")
            daily_loss_usd = data.get("daily_loss_usd")

        return {
            "mode": mode,
            "peak_equity_usd": peak_info.get("peak_usd"),
            "drawdown_ratio": peak_info.get("drawdown_ratio"),
            "ic_decay_streak": streak_info.get("streak", 0),
            "daily_loss_usd": daily_loss_usd,
        }
    except Exception as exc:
        return {"error": str(exc)}


def get_recent_trades(limit: int = 5) -> list:
    """Get recent trade executions from exec.reports stream."""
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        rows = r.xrevrange("exec.reports", count=200)
        trades = []
        for row in rows:
            _, fields = row
            payload = json.loads(fields.get("p", "{}"))
            if not isinstance(payload, dict):
                continue
            env = payload.get("env", {})
            data = payload.get("data", {})
            
            # Only include actual trades (FILLED or PARTIAL status)
            status = data.get("status", "")
            if status not in ("FILLED", "PARTIAL"):
                continue
            
            # Skip heartbeat orders
            client_order_id = data.get("client_order_id", "")
            if "heartbeat" in client_order_id.lower():
                continue
            
            filled_qty = data.get("filled_qty", 0)
            if filled_qty == 0:
                continue
            
            trade = {
                "ts": env.get("ts", ""),
                "symbol": data.get("symbol", ""),
                "status": status,
                "side": data.get("side", "UNKNOWN"),
                "filled_qty": filled_qty,
                "avg_px": data.get("avg_px", 0),
                "fee": data.get("fee", 0),
            }
            trades.append(trade)
            
            if len(trades) >= limit:
                break
        
        return trades
    except Exception as exc:
        return [{"error": f"redis_error: {exc}"}]


def main() -> int:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    stamp = now.strftime("%Y%m%dT%H%MZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"hourly_health_{stamp}.md"

    status = run_cmd("./scripts/run_local.sh status")
    alert = run_cmd("./.venv/bin/python scripts/live_alert_check.py")
    smoke = run_cmd("./.venv/bin/python scripts/live_smoke_check.py")
    git_head = run_cmd("git rev-parse --short HEAD")
    git_branch = run_cmd("git rev-parse --abbrev-ref HEAD")

    payload = read_current_version()
    version_lines = []
    if "error" in payload:
        version_lines.append(f"error={payload['error']}")
    else:
        version_lines.append(f"version={payload.get('version', '')}")
        version_lines.append(f"stage={payload.get('stage', '')}")
        version_lines.append(f"promoted_at={payload.get('promoted_at', '')}")
        version_lines.append(f"previous_version={payload.get('previous_version', '')}")
        version_lines.append(f"reason={payload.get('reason', '')}")

    ts_map = {
        "md.features.15m": stream_ts("md.features.15m"),
        "alpha.target": stream_ts("alpha.target"),
        "risk.approved": stream_ts("risk.approved"),
        "exec.reports": stream_ts("exec.reports"),
    }

    # Get account snapshot
    account = get_account_snapshot()
    account_lines = []
    if "error" in account:
        account_lines.append(f"error={account['error']}")
    else:
        equity = account.get("equity_usd", 0)
        cash = account.get("cash_usd", 0)
        positions = account.get("positions", {})
        health = account.get("health", {})
        reconcile_ok = health.get("reconcile_ok", "N/A")
        
        account_lines.append(f"equity_usd: {equity:.2f}")
        account_lines.append(f"cash_usd: {cash:.2f}")
        account_lines.append(f"reconcile_ok: {reconcile_ok}")
        account_lines.append("")
        account_lines.append("positions:")
        
        total_pnl = 0.0
        for sym, pos in positions.items():
            qty = pos.get("qty", 0)
            if qty != 0:
                entry_px = pos.get("entry_px", 0)
                mark_px = pos.get("mark_px", 0)
                unreal_pnl = pos.get("unreal_pnl", 0)
                side = pos.get("side", "UNKNOWN")
                total_pnl += unreal_pnl
                account_lines.append(f"  {sym}: {side} {abs(qty):.6f} @ {entry_px:.2f} | mark={mark_px:.2f} | pnl={unreal_pnl:.4f}")
        
        if not any(p.get("qty", 0) != 0 for p in positions.values()):
            account_lines.append("  (no open positions)")
        
        account_lines.append(f"")
        account_lines.append(f"total_unreal_pnl: {total_pnl:.4f}")

    # Compute signal IC
    ic_result = compute_signal_ic(n_obs=72)

    # Risk Guard Status (M1 guards)
    risk_guard = get_risk_guard_status()

    # Get recent trades
    recent_trades = get_recent_trades(5)
    trade_lines = []
    if not recent_trades:
        trade_lines.append("(no recent trades)")
    elif recent_trades and "error" in recent_trades[0]:
        trade_lines.append(recent_trades[0]["error"])
    else:
        for trade in recent_trades:
            ts = trade.get("ts", "")
            sym = trade.get("symbol", "")
            side = trade.get("side", "UNKNOWN")
            qty = trade.get("filled_qty", 0)
            px = trade.get("avg_px", 0)
            fee = trade.get("fee", 0)
            status = trade.get("status", "")
            trade_lines.append(f"{ts} | {sym} | {side} {qty:.6f} @ {px:.2f} | fee={fee:.4f} | {status}")
        
        if not trade_lines:
            trade_lines.append("(no recent trades)")

    # Build IC section lines
    ic_lines = []
    if "error" in ic_result:
        ic_lines.append(f"error: {ic_result['error']}")
    else:
        def _fmt_ic(v):
            return f"{v:+.4f}" if v is not None else "N/A (insufficient data)"
        ic_lines.append(f"IC_1h (rolling 72h): {_fmt_ic(ic_result.get('ic_1h'))}  [n_pairs={ic_result.get('n_pairs_1h', 0)}]")
        ic_lines.append(f"IC_4h (rolling 72h): {_fmt_ic(ic_result.get('ic_4h'))}  [n_pairs={ic_result.get('n_pairs_4h', 0)}]")
        ic_lines.append(f"per-factor IC (1h): mom={_fmt_ic(ic_result.get('ic_mom'))} supp={_fmt_ic(ic_result.get('ic_supp'))} macd={_fmt_ic(ic_result.get('ic_macd'))}")
        ic_lines.append(f"interpretation: IC > 0 = signal predictive, IC < -0.03 = degraded, trigger re-research at IC < -0.05")
        if ic_result.get("alert"):
            ic_lines.append(f"ALERT: {ic_result['alert']}")

    overall_ok = status.code == 0 and alert.code == 0 and smoke.code == 0

    content = [
        f"# Hourly Trading Health Report ({stamp})",
        "",
        f"- generated_at_utc: `{now.isoformat().replace('+00:00', 'Z')}`",
        f"- repo: `{ROOT}`",
        f"- branch: `{git_branch.out or 'unknown'}`",
        f"- commit: `{git_head.out or 'unknown'}`",
        f"- overall: `{'PASS' if overall_ok else 'FAIL'}`",
        "",
        "## Account Status",
        "```text",
        *account_lines,
        "```",
        "",
        "## Factor IC Tracking (Layer-1 Signal Quality)",
        "```text",
        *ic_lines,
        "```",
        "",
        "## Risk Guard Status",
        "```text",
        *(
            [f"error: {risk_guard['error']}"]
            if "error" in risk_guard
            else [
                f"mode:             {risk_guard.get('mode', 'UNKNOWN')}",
                f"peak_equity_usd:  {risk_guard.get('peak_equity_usd', 'N/A')}",
                f"drawdown_ratio:   {f\"{risk_guard['drawdown_ratio']:.4f}\" if risk_guard.get('drawdown_ratio') is not None else 'N/A'}",
                f"ic_decay_streak:  {risk_guard.get('ic_decay_streak', 0)}",
                f"daily_loss_usd:   {f\"{risk_guard['daily_loss_usd']:.4f}\" if risk_guard.get('daily_loss_usd') is not None else 'N/A'}",
            ]
        ),
        "```",
        "",
        "## Recent Trades (Last 5)",
        "```text",
        *trade_lines,
        "```",
        "",
        "## Current Deployment Version",
        "```text",
        *version_lines,
        "```",
        "",
        "## Stream Freshness (Latest env.ts)",
        "```text",
        *(f"{k}: {v}" for k, v in ts_map.items()),
        "```",
        "",
        "## Service Status",
        "```text",
        status.out or "(empty)",
        "```",
        "",
        "## Live Alert Check",
        f"- exit_code: `{alert.code}`",
        "```text",
        alert.out or "(empty)",
        "```",
    ]

    if alert.err:
        content.extend(["", "stderr:", "```text", alert.err, "```"])

    content.extend(
        [
            "",
            "## Live Smoke Check",
            f"- exit_code: `{smoke.code}`",
            "```text",
            smoke.out or "(empty)",
            "```",
        ]
    )
    if smoke.err:
        content.extend(["", "stderr:", "```text", smoke.err, "```"])

    report_path.write_text("\n".join(content) + "\n", encoding="utf-8")
    LATEST_PATH.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(str(report_path))
    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
