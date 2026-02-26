from datetime import datetime, timezone, timedelta

import pytest

from shared.ops_tools import (
    normalize_ctl_command,
    build_ctl_message,
    age_seconds,
    pipeline_lag_issues,
    count_exec_statuses,
    percentile,
    summarize_exec_quality,
    summarize_market_feature_quality,
    count_events,
    evaluate_alert_thresholds,
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


def test_percentile():
    assert percentile([], 0.95) is None
    assert percentile([1, 2, 3, 4], 0.95) == 4
    assert percentile([10, 20, 30, 40], 0.5) == 20


def test_summarize_exec_quality():
    q = summarize_exec_quality(
        [
            {"status": "ACK", "latency_ms": 100},
            {"status": "REJECTED", "latency_ms": 200},
            {"status": "REJECTED", "latency_ms": 400},
            {"status": "FILLED"},
        ]
    )
    assert q["total_reports"] == 4.0
    assert q["rejected_reports"] == 2.0
    assert q["reject_rate"] == 0.5
    assert q["p95_latency_ms"] == 400.0


def test_count_events():
    out = count_events(
        [
            {"event": "retry"},
            {"event": "retry"},
            {"event": "dlq"},
            {"x": 1},
        ]
    )
    assert out["retry"] == 2
    assert out["dlq"] == 1


def test_evaluate_alert_thresholds():
    reasons = evaluate_alert_thresholds(
        lag_issues={},
        reject_rate=0.8,
        p95_latency_ms=6000,
        dlq_events=1,
        max_reject_rate=0.7,
        max_p95_latency_ms=5000,
        max_dlq_events=0,
    )
    assert "reject_rate>0.70" in reasons
    assert "p95_latency_ms>5000" in reasons
    assert "dlq_events>0" in reasons


def test_summarize_market_feature_quality():
    out = summarize_market_feature_quality(
        [
            {
                "data": {
                    "basis_bps": {"BTC": 40.0, "ETH": -20.0},
                    "liquidity_score": {"BTC": 0.6, "ETH": 0.4},
                    "slippage_bps_15m": {"BTC": 3.0, "ETH": 2.0},
                }
            }
        ]
    )
    assert out["basis_bps_abs_avg"] == 30.0
    assert out["liquidity_score_avg"] == 0.5
    assert out["slippage_bps_15m_avg"] == 2.5


def test_evaluate_alert_thresholds_with_market_quality():
    reasons = evaluate_alert_thresholds(
        lag_issues={},
        reject_rate=0.1,
        p95_latency_ms=1000.0,
        dlq_events=0,
        max_reject_rate=0.7,
        max_p95_latency_ms=5000,
        max_dlq_events=0,
        basis_bps_abs_avg=90.0,
        max_basis_bps_abs_avg=60.0,
        liquidity_score_avg=0.05,
        min_liquidity_score_avg=0.1,
        slippage_bps_15m_avg=10.0,
        max_slippage_bps_15m_avg=8.0,
    )
    assert "basis_bps_abs_avg>60.00" in reasons
    assert "liquidity_score_avg<0.10" in reasons
    assert "slippage_bps_15m_avg>8.00" in reasons
