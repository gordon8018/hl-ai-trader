from __future__ import annotations

from typing import Dict, List, Tuple, Any


def summarize_exec(status_counts: Dict[str, int]) -> Dict[str, int]:
    ack = int(status_counts.get("ACK", 0))
    partial = int(status_counts.get("PARTIAL", 0))
    filled = int(status_counts.get("FILLED", 0))
    canceled = int(status_counts.get("CANCELED", 0))
    rejected = int(status_counts.get("REJECTED", 0))
    terminal = filled + canceled + rejected
    return {
        "ack": ack,
        "partial": partial,
        "filled": filled,
        "canceled": canceled,
        "rejected": rejected,
        "terminal": terminal,
    }


def evaluate_gates(
    *,
    lag_issues: Dict[str, str],
    state_issue: str,
    exec_summary: Dict[str, int],
    error_events: int,
    min_ack: int,
    max_rejected: int,
    max_error_events: int,
    require_terminal: bool,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if lag_issues:
        reasons.append(f"pipeline_lag_issues={lag_issues}")
    if state_issue and state_issue != "none":
        reasons.append(f"state_issue={state_issue}")
    if exec_summary.get("ack", 0) < min_ack:
        reasons.append(f"ack<{min_ack}")
    if exec_summary.get("rejected", 0) > max_rejected:
        reasons.append(f"rejected>{max_rejected}")
    if error_events > max_error_events:
        reasons.append(f"audit.error>{max_error_events}")
    if require_terminal and exec_summary.get("terminal", 0) <= 0:
        reasons.append("terminal=0")
    return (len(reasons) == 0), reasons


def render_markdown_report(report: Dict[str, Any]) -> str:
    verdict = "PASS" if report.get("pass") else "FAIL"
    lines = []
    lines.append("# Live Acceptance Report")
    lines.append("")
    lines.append(f"- Verdict: **{verdict}**")
    lines.append(f"- Generated At: `{report.get('generated_at', '')}`")
    lines.append(f"- Window: `{report.get('window_start', '')}` -> `{report.get('window_end', '')}`")
    lines.append("")
    lines.append("## Gates")
    reasons = report.get("fail_reasons", [])
    if reasons:
        for r in reasons:
            lines.append(f"- FAIL: `{r}`")
    else:
        lines.append("- All gates passed")
    lines.append("")
    lines.append("## Pipeline")
    lines.append(f"- Stream Lags: `{report.get('lag_issues', {})}`")
    lines.append(f"- State Issue: `{report.get('state_issue', 'none')}`")
    lines.append("")
    lines.append("## Execution")
    exec_summary = report.get("exec_summary", {})
    lines.append(f"- ACK: `{exec_summary.get('ack', 0)}`")
    lines.append(f"- PARTIAL: `{exec_summary.get('partial', 0)}`")
    lines.append(f"- FILLED: `{exec_summary.get('filled', 0)}`")
    lines.append(f"- CANCELED: `{exec_summary.get('canceled', 0)}`")
    lines.append(f"- REJECTED: `{exec_summary.get('rejected', 0)}`")
    lines.append(f"- TERMINAL: `{exec_summary.get('terminal', 0)}`")
    lines.append("")
    lines.append("## Audit")
    lines.append(f"- error events: `{report.get('error_events', 0)}`")
    lines.append(f"- retry events: `{report.get('retry_events', 0)}`")
    lines.append(f"- dlq events: `{report.get('dlq_events', 0)}`")
    lines.append("")
    lines.append("## Cycle Coverage")
    coverage = report.get("cycle_coverage", {})
    for stream, n in sorted(coverage.items()):
        lines.append(f"- `{stream}`: `{n}` cycles")
    lines.append("")
    lines.append("## Raw Status Counts")
    lines.append("```json")
    lines.append(str(report.get("status_counts", {})).replace("'", "\""))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
