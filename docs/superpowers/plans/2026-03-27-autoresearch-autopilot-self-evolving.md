# Autoresearch Autopilot Self-Evolving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 autoresearch 与实盘执行融合为“候选->影子->小仓->稳定/回滚”的自动进化闭环，并提供 Redis+CLI 的当前线上版本可见性。

**Architecture:** 新增部署状态机与编排器，统一负责候选评估、状态迁移、自动晋级和自动回滚。状态与审计写入 Redis/audit，运行态版本通过 `deployment.current_version` 对外暴露。保留人工可控开关并默认关闭自动晋级。

**Tech Stack:** Python 3.12, Redis, sqlite3, pytest, existing services (ai_decision/risk_engine/execution)

---

## File Structure

- Create: `research/deployment_state.py`
  - 责任：状态枚举、合法迁移校验、状态结构
- Create: `research/promotion_orchestrator.py`
  - 责任：影子评估、canary 晋级、回滚触发
- Create: `scripts/show_current_version.py`
  - 责任：查询并打印 `deployment.current_version`
- Create: `tests/research/test_deployment_state.py`
- Create: `tests/research/test_promotion_orchestrator.py`
- Create: `tests/scripts/test_show_current_version.py`
- Modify: `config/trading_params.json`
  - 责任：自动模式开关与门禁阈值参数（默认关闭）
- Modify: `docs/runbook.md`
  - 责任：自动模式启停、影子/小仓流程、回滚流程

---

### Task 1: 部署状态机基础

**Files:**
- Create: `tests/research/test_deployment_state.py`
- Create: `research/deployment_state.py`

- [ ] **Step 1: 写失败测试（合法与非法迁移）**

```python
from research.deployment_state import can_transition

def test_can_transition_shadow_to_canary():
    assert can_transition("shadow_running", "canary_live") is True

def test_cannot_transition_candidate_to_stable():
    assert can_transition("research_candidate", "stable") is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/research/test_deployment_state.py`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现状态机最小模块**

```python
VALID_TRANSITIONS = {
    "research_candidate": {"shadow_running"},
    "shadow_running": {"canary_live", "rollback"},
    "canary_live": {"stable", "rollback"},
    "rollback": {"stable"},
    "stable": set(),
}

def can_transition(src: str, dst: str) -> bool:
    return dst in VALID_TRANSITIONS.get(src, set())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/research/test_deployment_state.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/research/test_deployment_state.py research/deployment_state.py
git commit -m "feat(research): add deployment state machine primitives"
```

---

### Task 2: 当前版本可见性（Redis + CLI）

**Files:**
- Create: `scripts/show_current_version.py`
- Create: `tests/scripts/test_show_current_version.py`

- [ ] **Step 1: 写失败测试（结构化输出）**

```python
from scripts.show_current_version import format_version_payload

def test_format_version_payload():
    text = format_version_payload({"version": "V9_ar_1", "stage": "canary_live"})
    assert "V9_ar_1" in text
    assert "canary_live" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/scripts/test_show_current_version.py`  
Expected: FAIL

- [ ] **Step 3: 实现 CLI 脚本（支持 redis-cli 兼容）**

```python
def format_version_payload(payload: dict) -> str:
    return (
        f"version={payload.get('version','')}\n"
        f"stage={payload.get('stage','')}\n"
        f"promoted_at={payload.get('promoted_at','')}\n"
        f"previous_version={payload.get('previous_version','')}\n"
        f"reason={payload.get('reason','')}"
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/scripts/test_show_current_version.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/show_current_version.py tests/scripts/test_show_current_version.py
git commit -m "feat(script): add current deployment version viewer"
```

---

### Task 3: 自动门禁与回滚编排器骨架

**Files:**
- Create: `research/promotion_orchestrator.py`
- Create: `tests/research/test_promotion_orchestrator.py`

- [ ] **Step 1: 写失败测试（门禁失败触发回滚）**

```python
from research.promotion_orchestrator import evaluate_and_decide

def test_evaluate_and_decide_rolls_back_on_threshold_breach():
    out = evaluate_and_decide(
        metrics={"drawdown": 0.25, "reject_rate": 0.10, "slippage_bps": 12.0},
        thresholds={"max_drawdown": 0.20, "max_reject_rate": 0.05, "max_slippage_bps": 8.0},
    )
    assert out["action"] == "rollback"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/research/test_promotion_orchestrator.py`  
Expected: FAIL

- [ ] **Step 3: 实现最小编排决策**

```python
def evaluate_and_decide(metrics: dict, thresholds: dict) -> dict:
    if float(metrics.get("drawdown", 1.0)) > float(thresholds.get("max_drawdown", 0.2)):
        return {"action": "rollback", "reason": "drawdown_breach"}
    if float(metrics.get("reject_rate", 1.0)) > float(thresholds.get("max_reject_rate", 0.05)):
        return {"action": "rollback", "reason": "reject_rate_breach"}
    if float(metrics.get("slippage_bps", 999.0)) > float(thresholds.get("max_slippage_bps", 8.0)):
        return {"action": "rollback", "reason": "slippage_breach"}
    return {"action": "promote", "reason": "gate_pass"}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest -q tests/research/test_promotion_orchestrator.py`  
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add research/promotion_orchestrator.py tests/research/test_promotion_orchestrator.py
git commit -m "feat(research): add autopilot gate and rollback decision skeleton"
```

---

### Task 4: 配置接入（自动模式默认关闭）

**Files:**
- Modify: `config/trading_params.json`

- [ ] **Step 1: 添加自动模式参数（默认关闭）**

```json
{
  "AUTO_EVOLVE_ENABLED": false,
  "AUTO_SHADOW_HOURS": 24,
  "AUTO_CANARY_MAX_GROSS": 0.10,
  "AUTO_GATE_MAX_DRAWDOWN": 0.20,
  "AUTO_GATE_MAX_REJECT_RATE": 0.05,
  "AUTO_GATE_MAX_SLIPPAGE_BPS": 8.0
}
```

- [ ] **Step 2: 校验 JSON**

Run: `python3 -m json.tool config/trading_params.json >/dev/null && echo json_ok`  
Expected: `json_ok`

- [ ] **Step 3: 提交**

```bash
git add config/trading_params.json
git commit -m "chore(config): add autopilot evolution controls with safe defaults"
```

---

### Task 5: 运行手册与操作流程

**Files:**
- Modify: `docs/runbook.md`

- [ ] **Step 1: 新增 Autopilot 章节**

```markdown
## Autopilot Evolution Flow
1. candidate -> shadow_running (24h)
2. gate pass -> canary_live (small gross)
3. gate fail -> rollback to previous stable
4. canary pass -> stable
```

- [ ] **Step 2: 新增版本查询命令**

```markdown
- `redis-cli GET deployment.current_version`
- `python3 scripts/show_current_version.py`
```

- [ ] **Step 3: 提交**

```bash
git add docs/runbook.md
git commit -m "docs(runbook): add autopilot rollout and rollback procedures"
```

---

### Task 6: 端到端验收与证据

**Files:**
- Create: `docs/reports/autopilot_smoke_*.md` (runtime artifact)

- [ ] **Step 1: 执行核心测试**

Run:
- `pytest -q tests/research/test_deployment_state.py tests/research/test_promotion_orchestrator.py tests/scripts/test_show_current_version.py`
- `pytest -q tests/research/test_strategy_score.py tests/research/test_candidate_pack.py tests/research/test_promotion_gate.py`

Expected: PASS

- [ ] **Step 2: 产出 smoke 报告**

记录：
1. 当前版本可见性输出示例
2. 一次门禁通过与一次回滚判定样例
3. 配置关键开关值

- [ ] **Step 3: 提交验收报告**

```bash
git add docs/reports
git commit -m "docs(report): add autopilot smoke verification artifact"
```

---

## Self-Review Checklist

1. **Spec coverage**
- 状态机：Task 1
- Redis+CLI 版本可见：Task 2/5
- 自动门禁与回滚：Task 3
- 自动模式安全开关：Task 4
- 验收证据：Task 6

2. **Placeholder scan**
- 无 `TBD/TODO/implement later` 占位内容。

3. **Type consistency**
- `can_transition`、`evaluate_and_decide`、`format_version_payload` 在测试与实现中签名一致。

