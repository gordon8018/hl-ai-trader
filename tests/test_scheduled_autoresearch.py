"""Unit tests for scripts/scheduled_autoresearch.py trigger logic."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Load the module without needing a real Redis connection at import time
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "scheduled_autoresearch",
    ROOT / "scripts" / "scheduled_autoresearch.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Minimal fake Redis
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self, kv: Optional[dict] = None):
        self._kv: dict[str, Any] = kv or {}

    def get(self, key: str) -> Optional[str]:
        v = self._kv.get(key)
        return v if v is None else str(v)

    def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        self._kv[key] = value

    def ping(self) -> bool:
        return True


def _ts(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Tests: _days_since_last_run
# ---------------------------------------------------------------------------

def test_days_since_never_run():
    r = FakeRedis()
    days, reason = mod._days_since_last_run(r)
    assert days == float("inf")
    assert reason is None


def test_days_since_recent_run():
    r = FakeRedis({"autoresearch.last_run_ts": _ts(3.0), "autoresearch.last_trigger_reason": "biweekly"})
    days, reason = mod._days_since_last_run(r)
    assert 2.9 < days < 3.1
    assert reason == "biweekly"


# ---------------------------------------------------------------------------
# Tests: _check_ic_decay
# ---------------------------------------------------------------------------

def test_ic_decay_no_data():
    triggered, _ = mod._check_ic_decay(FakeRedis())
    assert not triggered


def test_ic_decay_positive_ic():
    r = FakeRedis({"ic.signal_ic_latest": json.dumps({"ic_1h": 0.03, "ts": "t1"})})
    triggered, _ = mod._check_ic_decay(r)
    assert not triggered


def test_ic_decay_below_trigger():
    r = FakeRedis({"ic.signal_ic_latest": json.dumps({"ic_1h": -0.06, "ts": "t1"})})
    triggered, reason = mod._check_ic_decay(r)
    assert triggered
    assert "ic_decay" in reason
    assert "-0.0600" in reason


def test_ic_streak_triggers():
    r = FakeRedis({
        "ic.signal_ic_latest": json.dumps({"ic_1h": -0.02, "ts": "t1"}),  # not below trigger
        "risk.ic_decay_streak": json.dumps({"streak": 3, "last_ic_ts": "t0", "ic_1h": -0.04}),
    })
    triggered, reason = mod._check_ic_decay(r)
    assert triggered
    assert "ic_streak" in reason


def test_ic_streak_below_threshold_no_trigger():
    r = FakeRedis({
        "ic.signal_ic_latest": json.dumps({"ic_1h": -0.02, "ts": "t1"}),
        "risk.ic_decay_streak": json.dumps({"streak": 2, "last_ic_ts": "t0", "ic_1h": -0.04}),
    })
    triggered, _ = mod._check_ic_decay(r)
    assert not triggered


# ---------------------------------------------------------------------------
# Tests: decide_trigger (integration of all conditions)
# ---------------------------------------------------------------------------

def test_decide_never_run_triggers_biweekly():
    r = FakeRedis()  # no last_run → inf days → biweekly triggers
    triggered, reason = mod.decide_trigger(r)
    assert triggered
    assert "biweekly_schedule" in reason


def test_decide_within_cooldown_blocks():
    r = FakeRedis({
        "autoresearch.last_run_ts": _ts(2.0),  # 2 days ago < 7-day cooldown
        "ic.signal_ic_latest": json.dumps({"ic_1h": -0.10, "ts": "t1"}),  # IC very bad
    })
    triggered, reason = mod.decide_trigger(r)
    assert not triggered
    assert "cooldown" in reason


def test_decide_biweekly_after_14_days():
    r = FakeRedis({"autoresearch.last_run_ts": _ts(15.0)})
    triggered, reason = mod.decide_trigger(r)
    assert triggered
    assert "biweekly_schedule" in reason


def test_decide_ic_decay_after_cooldown():
    r = FakeRedis({
        "autoresearch.last_run_ts": _ts(8.0),  # 8 days ago > 7-day cooldown
        "ic.signal_ic_latest": json.dumps({"ic_1h": -0.06, "ts": "t1"}),
    })
    triggered, reason = mod.decide_trigger(r)
    assert triggered
    assert "ic_decay" in reason


def test_decide_no_trigger_good_ic():
    r = FakeRedis({
        "autoresearch.last_run_ts": _ts(8.0),
        "ic.signal_ic_latest": json.dumps({"ic_1h": 0.04, "ts": "t1"}),
    })
    triggered, reason = mod.decide_trigger(r)
    assert not triggered
    assert "no_trigger" in reason


# ---------------------------------------------------------------------------
# Tests: record_run
# ---------------------------------------------------------------------------

def test_record_run_stores_timestamp_and_reason():
    r = FakeRedis()
    mod.record_run(r, "biweekly_schedule")
    assert r._kv.get("autoresearch.last_run_ts") is not None
    assert r._kv.get("autoresearch.last_trigger_reason") == "biweekly_schedule"
