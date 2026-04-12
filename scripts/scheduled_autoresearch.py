#!/usr/bin/env python3
"""
L1: Scheduled Autoresearch — checks trigger conditions, runs research when warranted.

Trigger hierarchy (first match wins):
  1. Biweekly schedule    — 14+ days since last research run
  2. IC decay             — IC_1h from ic.signal_ic_latest < IC_DECAY_TRIGGER (default -0.05)
  3. IC streak            — risk.ic_decay_streak streak >= 3 AND ic_1h < -0.03

Cooldown: minimum MIN_COOLDOWN_DAYS between consecutive runs regardless of trigger type.
State persisted in Redis: autoresearch.last_run_ts / autoresearch.last_trigger_reason.

Output: 0 = ran (or skipped with --dry-run), 1 = error, 2 = cooldown / no trigger.
Manual approval is always required before promotion.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import redis

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

# Thresholds (can be overridden via env)
BIWEEKLY_DAYS          = int(os.environ.get("AR_BIWEEKLY_DAYS", "14"))
MIN_COOLDOWN_DAYS      = int(os.environ.get("AR_MIN_COOLDOWN_DAYS", "7"))
IC_DECAY_TRIGGER       = float(os.environ.get("AR_IC_DECAY_TRIGGER", "-0.05"))
IC_STREAK_THRESHOLD    = int(os.environ.get("AR_IC_STREAK_THRESHOLD", "3"))
IC_STREAK_IC_THRESHOLD = float(os.environ.get("AR_IC_STREAK_IC_THRESHOLD", "-0.03"))


def _redis() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _days_since_last_run(r: redis.Redis) -> Tuple[float, Optional[str]]:
    """Return (days_since_last_run, last_trigger_reason). days=inf if never run."""
    raw_ts = r.get("autoresearch.last_run_ts")
    raw_reason = r.get("autoresearch.last_trigger_reason")
    if not raw_ts:
        return float("inf"), None
    try:
        last_run = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        delta = (datetime.now(timezone.utc) - last_run).total_seconds() / 86400.0
        return delta, raw_reason
    except Exception:
        return float("inf"), None


def _check_ic_decay(r: redis.Redis) -> Tuple[bool, str]:
    """Return (triggered, reason) for IC-based triggers."""
    raw = r.get("ic.signal_ic_latest")
    if not raw:
        return False, ""
    try:
        ic_data = json.loads(raw)
    except Exception:
        return False, ""

    ic_1h = ic_data.get("ic_1h")
    if ic_1h is not None and float(ic_1h) < IC_DECAY_TRIGGER:
        return True, f"ic_decay (ic_1h={float(ic_1h):+.4f} < {IC_DECAY_TRIGGER})"

    # Streak-based trigger
    streak_raw = r.get("risk.ic_decay_streak")
    if streak_raw:
        try:
            streak_data = json.loads(streak_raw)
            streak = int(streak_data.get("streak", 0))
            streak_ic = float(streak_data.get("ic_1h", 0.0))
            if streak >= IC_STREAK_THRESHOLD and streak_ic < IC_STREAK_IC_THRESHOLD:
                return True, (
                    f"ic_streak (streak={streak}>={IC_STREAK_THRESHOLD}, "
                    f"ic_1h={streak_ic:+.4f} < {IC_STREAK_IC_THRESHOLD})"
                )
        except Exception:
            pass

    return False, ""


def decide_trigger(r: redis.Redis) -> Tuple[bool, str]:
    """
    Evaluate all trigger conditions.
    Returns (should_run, reason_string).
    """
    days_since, last_reason = _days_since_last_run(r)

    # Hard cooldown guard — no reason overrides this
    if days_since < MIN_COOLDOWN_DAYS:
        return False, f"cooldown (last_run={days_since:.1f}d ago, min={MIN_COOLDOWN_DAYS}d)"

    # 1. Biweekly schedule
    if days_since >= BIWEEKLY_DAYS:
        return True, f"biweekly_schedule (last_run={days_since:.1f}d ago >= {BIWEEKLY_DAYS}d)"

    # 2 & 3. IC decay triggers
    ic_triggered, ic_reason = _check_ic_decay(r)
    if ic_triggered:
        return True, ic_reason

    return False, f"no_trigger (last_run={days_since:.1f}d ago)"


def record_run(r: redis.Redis, reason: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r.set("autoresearch.last_run_ts", now_iso, ex=60 * 86400)
    r.set("autoresearch.last_trigger_reason", reason, ex=60 * 86400)


def run_autoresearch(dry_run: bool, output_dir: Path) -> int:
    """Invoke run_autoresearch.py as subprocess. Returns exit code."""
    cmd = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(ROOT / "scripts" / "run_autoresearch.py"),
        "--output-dir", str(output_dir),
    ]
    print(f"[scheduled_autoresearch] Running: {' '.join(cmd)}")
    if dry_run:
        print("[scheduled_autoresearch] --dry-run: skipping actual subprocess call")
        return 0
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), timeout=600)
        return proc.returncode
    except subprocess.TimeoutExpired:
        print("[scheduled_autoresearch] ERROR: autoresearch timed out after 600s", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[scheduled_autoresearch] ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="L1 scheduled autoresearch launcher")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Check triggers and print decision without actually running research",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip trigger check and run immediately (ignores cooldown)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "docs" / "reports",
        help="Output directory for candidate packs",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[scheduled_autoresearch] {now}")
    print(f"[scheduled_autoresearch] thresholds: biweekly={BIWEEKLY_DAYS}d, cooldown={MIN_COOLDOWN_DAYS}d, "
          f"ic_trigger={IC_DECAY_TRIGGER}, ic_streak>={IC_STREAK_THRESHOLD}")

    try:
        r = _redis()
        r.ping()
    except Exception as exc:
        print(f"[scheduled_autoresearch] ERROR: cannot connect to Redis: {exc}", file=sys.stderr)
        return 1

    if args.force:
        triggered, reason = True, "forced_by_operator"
        print(f"[scheduled_autoresearch] FORCE override → triggering")
    else:
        triggered, reason = decide_trigger(r)

    print(f"[scheduled_autoresearch] decision: triggered={triggered} reason={reason}")

    if not triggered:
        print("[scheduled_autoresearch] No action — exiting.")
        return 2

    # Run
    print(f"\n[scheduled_autoresearch] === STARTING AUTORESEARCH === reason={reason}")
    rc = run_autoresearch(dry_run=args.dry_run, output_dir=args.output_dir)

    if rc == 0 and not args.dry_run:
        record_run(r, reason)
        print(f"[scheduled_autoresearch] Run complete. State recorded in Redis.")
        print("[scheduled_autoresearch] REMINDER: manual approval required before promotion.")
    elif args.dry_run:
        print("[scheduled_autoresearch] Dry-run complete — no state recorded.")
    else:
        print(f"[scheduled_autoresearch] autoresearch exited with code {rc}", file=sys.stderr)

    return rc if rc != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
