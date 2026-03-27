# Autoresearch Risk-Adjusted Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前交易项目中接入 autoresearch 离线优化闭环，生成“90天风险调整收益优先”的候选参数包，并通过人工审批后再发布。

**Architecture:** 新增 `research/` 目录承载评分器、候选包生成与运行入口。评分器从 `data/reporting.db` 与历史导出读取数据，输出风险调整分与约束惩罚。发布采用“门禁校验 + 人工审批 + profile 切换”，不允许自动上线。

**Tech Stack:** Python 3.12, sqlite3, Redis Streams (历史导出), pytest, JSON config versioning

---

## File Structure

- Create: `research/strategy_score.py`
  - 责任：计算 90 天风险调整主分、回撤惩罚、执行质量惩罚与稳定性惩罚
- Create: `research/candidate_pack.py`
  - 责任：将候选参数、指标、diff、风险说明打包落盘
- Create: `research/autoresearch_runner.py`
  - 责任：调用 autoresearch（或其适配层）迭代搜索参数并调用评分器产出候选
- Create: `research/promotion_gate.py`
  - 责任：按门禁规则校验候选是否可进入人工审批
- Create: `tests/research/test_strategy_score.py`
- Create: `tests/research/test_candidate_pack.py`
- Create: `tests/research/test_promotion_gate.py`
- Modify: `config/trading_params.json`
  - 责任：新增 `V9_ar_*` 示例段（不自动激活）
- Modify: `docs/runbook.md`
  - 责任：新增 AR 候选审核、上线和回滚流程

---

### Task 1: 建立评分器骨架（90天风险调整主分）

**Files:**
- Create: `tests/research/test_strategy_score.py`
- Create: `research/strategy_score.py`

- [ ] **Step 1: 写失败测试（主分单调性）**

```python
# tests/research/test_strategy_score.py
from research.strategy_score import evaluate_candidate


def test_score_prefers_better_return_and_lower_drawdown():
    base = {
        "daily_returns": [0.001] * 90,
        "max_drawdown": 0.20,
        "reject_rate": 0.01,
        "slippage_bps": 2.0,
    }
    better = {
        "daily_returns": [0.0015] * 90,
        "max_drawdown": 0.12,
        "reject_rate": 0.01,
        "slippage_bps": 2.0,
    }
    s_base = evaluate_candidate(base)["score_total"]
    s_better = evaluate_candidate(better)["score_total"]
    assert s_better > s_base
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/research/test_strategy_score.py::test_score_prefers_better_return_and_lower_drawdown`  
Expected: FAIL（`ModuleNotFoundError` 或函数不存在）

- [ ] **Step 3: 最小实现评分器**

```python
# research/strategy_score.py
from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev
from typing import Any, Dict, List


def _sharpe(daily_returns: List[float]) -> float:
    mu = mean(daily_returns) if daily_returns else 0.0
    sigma = pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
    if sigma <= 1e-12:
        return 0.0
    return (mu / sigma) * sqrt(252.0)


def evaluate_candidate(metrics: Dict[str, Any]) -> Dict[str, float]:
    returns = list(metrics.get("daily_returns", []))
    max_dd = float(metrics.get("max_drawdown", 1.0))
    reject_rate = float(metrics.get("reject_rate", 1.0))
    slippage_bps = float(metrics.get("slippage_bps", 999.0))

    sharpe = _sharpe(returns)
    calmar = (mean(returns) * 252.0) / max(max_dd, 1e-6) if returns else 0.0
    penalty_dd = max_dd * 3.0
    penalty_exec = reject_rate * 2.0 + (slippage_bps / 100.0)
    score_total = sharpe + calmar - penalty_dd - penalty_exec

    return {
        "score_total": score_total,
        "score_sharpe": sharpe,
        "score_calmar": calmar,
        "penalty_dd": penalty_dd,
        "penalty_exec": penalty_exec,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/research/test_strategy_score.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/research/test_strategy_score.py research/strategy_score.py
git commit -m "feat(research): add 90d risk-adjusted strategy scoring baseline"
```

---

### Task 2: 候选包生成器（参数+报告+diff+风险说明）

**Files:**
- Create: `tests/research/test_candidate_pack.py`
- Create: `research/candidate_pack.py`

- [ ] **Step 1: 写失败测试（输出文件完整性）**

```python
# tests/research/test_candidate_pack.py
from pathlib import Path
from research.candidate_pack import write_candidate_pack


def test_write_candidate_pack_creates_required_files(tmp_path: Path):
    out = write_candidate_pack(
        output_dir=tmp_path,
        profile_name="V9_ar_20260327_run1",
        candidate_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.03},
        metrics={"score_total": 1.23},
        baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
        risk_notes="drawdown stable",
    )
    assert (out / "candidate_profile.json").exists()
    assert (out / "report.md").exists()
    assert (out / "diff.md").exists()
    assert (out / "risk_notes.md").exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/research/test_candidate_pack.py::test_write_candidate_pack_creates_required_files`  
Expected: FAIL（函数不存在）

- [ ] **Step 3: 最小实现候选包生成**

```python
# research/candidate_pack.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _build_diff(candidate: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    keys = sorted(set(candidate) | set(baseline))
    lines = ["# Candidate Diff", "", "| key | baseline | candidate |", "|---|---:|---:|"]
    for k in keys:
        lines.append(f"| {k} | {baseline.get(k)} | {candidate.get(k)} |")
    return "\n".join(lines) + "\n"


def write_candidate_pack(
    output_dir: Path,
    profile_name: str,
    candidate_params: Dict[str, Any],
    metrics: Dict[str, Any],
    baseline_params: Dict[str, Any],
    risk_notes: str,
) -> Path:
    pack_dir = output_dir / profile_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    (pack_dir / "candidate_profile.json").write_text(
        json.dumps({"profile_name": profile_name, "params": candidate_params}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (pack_dir / "report.md").write_text(
        "# Candidate Report\n\n```json\n" + json.dumps(metrics, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    (pack_dir / "diff.md").write_text(_build_diff(candidate_params, baseline_params), encoding="utf-8")
    (pack_dir / "risk_notes.md").write_text(risk_notes + "\n", encoding="utf-8")
    return pack_dir
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/research/test_candidate_pack.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/research/test_candidate_pack.py research/candidate_pack.py
git commit -m "feat(research): add candidate pack writer for autoresearch outputs"
```

---

### Task 3: 发布门禁校验（仅允许进入人工审批）

**Files:**
- Create: `tests/research/test_promotion_gate.py`
- Create: `research/promotion_gate.py`

- [ ] **Step 1: 写失败测试（门禁规则）**

```python
# tests/research/test_promotion_gate.py
from research.promotion_gate import evaluate_promotion_gate


def test_gate_rejects_when_drawdown_worsens():
    decision = evaluate_promotion_gate(
        candidate={"score_total": 1.8, "max_drawdown": 0.24, "reject_rate": 0.01, "slippage_bps": 2.0},
        baseline={"score_total": 1.5, "max_drawdown": 0.18, "reject_rate": 0.01, "slippage_bps": 2.0},
        thresholds={"max_dd_delta": 0.00, "max_reject_rate": 0.05, "max_slippage_bps": 8.0},
    )
    assert decision["pass"] is False
    assert "drawdown_regression" in decision["reasons"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/research/test_promotion_gate.py::test_gate_rejects_when_drawdown_worsens`  
Expected: FAIL

- [ ] **Step 3: 最小实现门禁校验**

```python
# research/promotion_gate.py
from __future__ import annotations

from typing import Any, Dict, List


def evaluate_promotion_gate(
    candidate: Dict[str, Any],
    baseline: Dict[str, Any],
    thresholds: Dict[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []
    if float(candidate["score_total"]) <= float(baseline["score_total"]):
        reasons.append("score_not_improved")

    dd_delta = float(candidate["max_drawdown"]) - float(baseline["max_drawdown"])
    if dd_delta > float(thresholds["max_dd_delta"]):
        reasons.append("drawdown_regression")

    if float(candidate["reject_rate"]) > float(thresholds["max_reject_rate"]):
        reasons.append("reject_rate_too_high")

    if float(candidate["slippage_bps"]) > float(thresholds["max_slippage_bps"]):
        reasons.append("slippage_too_high")

    return {"pass": len(reasons) == 0, "reasons": reasons}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/research/test_promotion_gate.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/research/test_promotion_gate.py research/promotion_gate.py
git commit -m "feat(research): add promotion gate checks for manual approval flow"
```

---

### Task 4: autoresearch 运行入口（离线候选生成）

**Files:**
- Create: `research/autoresearch_runner.py`
- Modify: `research/strategy_score.py`

- [ ] **Step 1: 写失败测试（端到端最小运行）**

```python
# tests/research/test_strategy_score.py
from pathlib import Path
from research.autoresearch_runner import run_one_iteration


def test_run_one_iteration_returns_candidate(tmp_path: Path):
    out = run_one_iteration(
        output_root=tmp_path,
        baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
        observed_metrics={"daily_returns": [0.001] * 90, "max_drawdown": 0.18, "reject_rate": 0.01, "slippage_bps": 2.0},
    )
    assert out["profile_name"].startswith("V9_ar_")
    assert out["score"]["score_total"] is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/research/test_strategy_score.py::test_run_one_iteration_returns_candidate`  
Expected: FAIL

- [ ] **Step 3: 实现最小运行器（后续替换为真实 autoresearch 调用）**

```python
# research/autoresearch_runner.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from research.candidate_pack import write_candidate_pack
from research.strategy_score import evaluate_candidate


def _propose_params(baseline: Dict[str, Any]) -> Dict[str, Any]:
    proposed = dict(baseline)
    cur = float(proposed.get("AI_SIGNAL_DELTA_THRESHOLD", 0.05))
    proposed["AI_SIGNAL_DELTA_THRESHOLD"] = max(0.02, round(cur - 0.01, 4))
    return proposed


def run_one_iteration(
    output_root: Path,
    baseline_params: Dict[str, Any],
    observed_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_params = _propose_params(baseline_params)
    score = evaluate_candidate(observed_metrics)
    profile_name = "V9_ar_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pack_dir = write_candidate_pack(
        output_dir=output_root,
        profile_name=profile_name,
        candidate_params=candidate_params,
        metrics=score,
        baseline_params=baseline_params,
        risk_notes="manual approval required before promotion",
    )
    return {"profile_name": profile_name, "pack_dir": str(pack_dir), "score": score}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/research/test_strategy_score.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add research/autoresearch_runner.py research/strategy_score.py tests/research/test_strategy_score.py
git commit -m "feat(research): add offline autoresearch iteration runner skeleton"
```

---

### Task 5: 配置与运行手册接入（人工审批流程）

**Files:**
- Modify: `config/trading_params.json`
- Modify: `docs/runbook.md`

- [ ] **Step 1: 写文档与配置回归检查命令**

Run:
- `python3 -m json.tool config/trading_params.json >/dev/null`
- `rg -n "autoresearch|V9_ar|manual approval" docs/runbook.md`

Expected: 命令可执行

- [ ] **Step 2: 更新配置加入候选版本模板（不激活）**

```json
{
  "versions": {
    "V9_ar_template": {
      "_description": "autoresearch candidate template; manual approval required",
      "AI_SIGNAL_DELTA_THRESHOLD": 0.03,
      "MAX_GROSS_SIDEWAYS": 0.10,
      "MAX_TRADES_PER_DAY": 15
    }
  }
}
```

- [ ] **Step 3: 更新 runbook 的审批步骤**

```markdown
## AR Candidate Promotion (Manual Gate)
1. Run offline candidate generation and produce candidate pack.
2. Review 90d score, drawdown delta, reject/slippage metrics.
3. If and only if gate passes and human approves, add profile and switch active_version.
4. Monitor 4-8h; rollback immediately if guardrails are breached.
```

- [ ] **Step 4: 运行验证命令**

Run:
- `python3 -m json.tool config/trading_params.json >/dev/null && echo json_ok`
- `pytest -q tests/research/test_strategy_score.py tests/research/test_candidate_pack.py tests/research/test_promotion_gate.py`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add config/trading_params.json docs/runbook.md
git commit -m "docs(config): add manual autoresearch candidate promotion workflow"
```

---

### Task 6: 端到端验收（研究候选产出与门禁判断）

**Files:**
- Modify: `docs/runbook.md` (append acceptance commands if needed)
- Create: `docs/reports/autoresearch_candidate_*.md` (runtime output)

- [ ] **Step 1: 执行最小候选生成**

Run:
`python3 - <<'PY'
from pathlib import Path
from research.autoresearch_runner import run_one_iteration
out = run_one_iteration(
    output_root=Path("docs/reports"),
    baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
    observed_metrics={
      "daily_returns":[0.001]*90,
      "max_drawdown":0.18,
      "reject_rate":0.01,
      "slippage_bps":2.0
    },
)
print(out)
PY`

Expected: 输出 `profile_name` 和 `pack_dir`

- [ ] **Step 2: 手工门禁评估演练**

Run:
`python3 - <<'PY'
from research.promotion_gate import evaluate_promotion_gate
print(evaluate_promotion_gate(
  candidate={"score_total":1.8,"max_drawdown":0.18,"reject_rate":0.01,"slippage_bps":2.0},
  baseline={"score_total":1.5,"max_drawdown":0.18,"reject_rate":0.01,"slippage_bps":2.0},
  thresholds={"max_dd_delta":0.00,"max_reject_rate":0.05,"max_slippage_bps":8.0}
))
PY`

Expected: `{"pass": True, ...}`

- [ ] **Step 3: 提交验收补充（如有）**

```bash
git add docs/runbook.md docs/reports
git commit -m "docs: add autoresearch acceptance run artifacts"
```

---

## Self-Review Checklist

1. **Spec coverage**
- 搜索空间受控：Task 1/4/5
- 90 天风险调整评估：Task 1
- 候选包输出：Task 2
- 人工门禁：Task 3/5
- 发布与回滚流程：Task 5/6

2. **Placeholder scan**
- 无 `TBD/TODO/implement later` 占位词。
- 每个任务包含可执行命令与示例代码。

3. **Type consistency**
- `evaluate_candidate`、`write_candidate_pack`、`evaluate_promotion_gate`、`run_one_iteration` 在后续任务中的调用签名保持一致。

