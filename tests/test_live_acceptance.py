from shared.live_acceptance import summarize_exec, evaluate_gates, render_markdown_report


def test_summarize_exec():
    s = summarize_exec({"ACK": 2, "FILLED": 1, "REJECTED": 1, "CANCELED": 1})
    assert s["ack"] == 2
    assert s["terminal"] == 3
    assert s["rejected"] == 1


def test_evaluate_gates_pass():
    ok, reasons = evaluate_gates(
        lag_issues={},
        state_issue="none",
        exec_summary={"ack": 2, "terminal": 1, "rejected": 0},
        error_events=0,
        min_ack=1,
        max_rejected=0,
        max_error_events=0,
        require_terminal=False,
    )
    assert ok is True
    assert reasons == []


def test_evaluate_gates_fail():
    ok, reasons = evaluate_gates(
        lag_issues={"alpha.target": "stale:1000s"},
        state_issue="reconcile_unhealthy",
        exec_summary={"ack": 0, "terminal": 0, "rejected": 2},
        error_events=5,
        min_ack=1,
        max_rejected=0,
        max_error_events=0,
        require_terminal=True,
    )
    assert ok is False
    assert any("pipeline_lag_issues" in r for r in reasons)
    assert any("state_issue" in r for r in reasons)
    assert any("ack<" in r for r in reasons)
    assert any("rejected>" in r for r in reasons)
    assert any("audit.error>" in r for r in reasons)
    assert any("terminal=0" in r for r in reasons)


def test_render_markdown_report():
    report = {
        "pass": True,
        "generated_at": "2026-02-20T10:00:00Z",
        "window_start": "2026-02-20T09:30:00Z",
        "window_end": "2026-02-20T10:00:00Z",
        "fail_reasons": [],
        "lag_issues": {},
        "state_issue": "none",
        "exec_summary": {"ack": 1, "partial": 0, "filled": 1, "canceled": 0, "rejected": 0, "terminal": 1},
        "error_events": 0,
        "retry_events": 0,
        "dlq_events": 0,
        "cycle_coverage": {"md.features.1m": 10},
        "status_counts": {"ACK": 1, "FILLED": 1},
    }
    md = render_markdown_report(report)
    assert "Live Acceptance Report" in md
    assert "Verdict: **PASS**" in md
    assert "\"ACK\": 1" in md
