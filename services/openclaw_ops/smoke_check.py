from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Tuple

ISSUE_STREAM_LAG = "STREAM_LAG_TOO_HIGH"
ISSUE_STATE_TOO_OLD = "STATE_TOO_OLD"
ISSUE_EXEC_REPORTS_MISSING = "EXEC_REPORTS_MISSING"
ISSUE_SCRIPT_ERROR = "SCRIPT_ERROR"


def _parse_metric(line: str) -> Tuple[str, Any] | None:
    parts = line.split(":", 1)
    if len(parts) != 2:
        return None
    key = parts[0].strip()
    value = parts[1].strip()
    return key, value


def run_smoke_check(max_lag_sec: int, state_max_age_sec: int, report_sample: int) -> Tuple[Dict[str, Any], List[str]]:
    cmd = [
        "python",
        "scripts/live_smoke_check.py",
        "--max-lag-sec",
        str(max_lag_sec),
        "--state-max-age-sec",
        str(state_max_age_sec),
        "--report-sample",
        str(report_sample),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except Exception:
        return {"pass": False}, [ISSUE_SCRIPT_ERROR]

    if res.returncode not in (0, 2):
        return {
            "pass": False,
            "max_stream_lag_sec": None,
            "state_age_sec": None,
            "exec_reports_seen": None,
            "stdout": res.stdout[:2000],
            "stderr": res.stderr[:2000],
        }, [ISSUE_SCRIPT_ERROR]

    notes: List[str] = []
    stdout = res.stdout or ""
    metrics: Dict[str, Any] = {
        "pass": res.returncode == 0,
        "max_stream_lag_sec": None,
        "state_age_sec": None,
        "exec_reports_seen": None,
        "stdout": stdout[:2000],
    }

    for line in stdout.splitlines():
        if line.startswith("lag_issues:") and "none" not in line:
            notes.append(ISSUE_STREAM_LAG)
        if line.startswith("state_issue:") and "none" not in line:
            notes.append(ISSUE_STATE_TOO_OLD)
        if line.startswith("exec_report_status_counts:") and "none" in line:
            notes.append(ISSUE_EXEC_REPORTS_MISSING)

        parsed = _parse_metric(line)
        if not parsed:
            continue
        key, value = parsed
        if key == "lag_issues" and value and value != "none":
            match = re.findall(r"stale:(\d+\.?\d*)s", value)
            if match:
                metrics["max_stream_lag_sec"] = max(float(x) for x in match)
        if key == "state_issue" and value.startswith("state_stale"):
            m = re.findall(r"state_stale:(\d+\.?\d*)s", value)
            if m:
                metrics["state_age_sec"] = float(m[0])
        if key == "exec_report_status_counts" and value != "none":
            metrics["exec_reports_seen"] = True

    if not metrics["pass"] and ISSUE_STREAM_LAG not in notes and ISSUE_STATE_TOO_OLD not in notes:
        notes.append(ISSUE_SCRIPT_ERROR)

    metrics["exec_reports_seen"] = metrics["exec_reports_seen"] is True
    return metrics, notes
