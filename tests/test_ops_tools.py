from datetime import datetime, timezone, timedelta

import pytest

from shared.ops_tools import (
    normalize_ctl_command,
    build_ctl_message,
    age_seconds,
    pipeline_lag_issues,
    count_exec_statuses,
)


def test_normalize_ctl_command():
    assert normalize_ctl_command("halt") == "HALT"
    assert normalize_ctl_command("REDUCE_ONLY") == "REDUCE_ONLY"
    with pytest.raises(ValueError):
        normalize_ctl_command("bad")


def test_build_ctl_message():
    msg = build_ctl_message("resume", reason="recover")
    assert msg["data"]["cmd"] == "RESUME"
    assert msg["data"]["reason"] == "recover"
    assert msg["env"]["source"] == "ops.manual"


def test_age_seconds():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ts = (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    age = age_seconds(ts, now=now)
    assert age is not None
    assert 9.0 <= age <= 11.0


def test_pipeline_lag_issues():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ok = now.isoformat().replace("+00:00", "Z")
    stale = (now - timedelta(seconds=500)).isoformat().replace("+00:00", "Z")
    issues = pipeline_lag_issues(
        {"a": ok, "b": stale},
        required_streams=["a", "b", "c"],
        max_lag_sec=120,
    )
    assert "a" not in issues
    assert issues["b"].startswith("stale:")
    assert issues["c"] == "missing"


def test_count_exec_statuses():
    counts = count_exec_statuses(
        [
            {"status": "ACK"},
            {"status": "FILLED"},
            {"status": "FILLED"},
            {"status": "CANCELED"},
            {"x": 1},
        ]
    )
    assert counts["ACK"] == 1
    assert counts["FILLED"] == 2
    assert counts["CANCELED"] == 1
