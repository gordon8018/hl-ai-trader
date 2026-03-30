# Profit-Max With -5% Drawdown Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase effective trade frequency and 30-day return while enforcing hard drawdown limit above `-5%`.

**Architecture:** Introduce tiered entry logic in `ai_decision`, add drawdown-band risk budget controls in `risk_engine`, and add edge-aware execution filters in `execution`. Extend reporting fields so promotion gates can compare return uplift against risk and execution quality regression. Roll out in phased gating with explicit rollback triggers.

**Tech Stack:** Python 3.11, Redis Streams, SQLite (`reporting.db`), pytest

---

### Task 1: Add Tiered Entry Logic in AI Decision

**Files:**
- Modify: `services/ai_decision/app.py`
- Test: `tests/test_ai_decision_utils.py`

- [ ] **Step 1: Write failing tests for decision tier classification**

```python
def test_decision_tier_strong_signal():
    tier = classify_decision_tier(signal=0.85, trend_agree=True, liquidity_score=0.7)
    assert tier == "strong_signal"

def test_decision_tier_medium_requires_filters():
    tier = classify_decision_tier(signal=0.55, trend_agree=True, liquidity_score=0.4)
    assert tier == "medium_signal"

def test_decision_tier_weak_defaults_hold():
    tier = classify_decision_tier(signal=0.2, trend_agree=False, liquidity_score=0.2)
    assert tier == "weak_signal"
```

- [ ] **Step 2: Run test to verify failure**

Run: `./.venv/bin/pytest -q tests/test_ai_decision_utils.py -k decision_tier`  
Expected: FAIL with missing `classify_decision_tier`.

- [ ] **Step 3: Implement minimal tier function and evidence fields**

```python
def classify_decision_tier(signal: float, trend_agree: bool, liquidity_score: float) -> str:
    if signal >= 0.75:
        return "strong_signal"
    if signal >= 0.45 and trend_agree and liquidity_score >= 0.30:
        return "medium_signal"
    return "weak_signal"
```

Add to `evidence` payload:
- `decision_tier`
- `expected_edge_bps`
- `entry_gate_reason`

- [ ] **Step 4: Enforce weak-tier HOLD and medium-tier filter**

Rules in handler:
- `strong_signal`: proceed to existing rebalance logic
- `medium_signal`: only proceed if execution-quality deltas are not degraded
- `weak_signal`: set HOLD (`decision_action="HOLD"`)

- [ ] **Step 5: Re-run tests**

Run: `./.venv/bin/pytest -q tests/test_ai_decision_utils.py`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_utils.py
git commit -m "feat(ai): add tiered entry gating with expected edge evidence"
```

### Task 2: Add Drawdown-Band Risk Budget Controls

**Files:**
- Modify: `services/risk_engine/app.py`
- Test: `tests/test_risk_engine_utils.py`

- [ ] **Step 1: Write failing tests for drawdown mode transitions**

```python
def test_drawdown_band_normal():
    assert mode_from_drawdown(0.02) == "NORMAL"

def test_drawdown_band_reduce_only():
    assert mode_from_drawdown(0.045) == "REDUCE_ONLY"

def test_drawdown_band_halt():
    assert mode_from_drawdown(0.051) == "HALT"
```

- [ ] **Step 2: Run test to verify failure**

Run: `./.venv/bin/pytest -q tests/test_risk_engine_utils.py -k drawdown_band`  
Expected: FAIL with missing `mode_from_drawdown`.

- [ ] **Step 3: Implement drawdown computation from rolling peak equity**

```python
def mode_from_drawdown(drawdown_ratio: float) -> str:
    if drawdown_ratio >= 0.05:
        return "HALT"
    if drawdown_ratio > 0.04:
        return "REDUCE_ONLY"
    return "NORMAL"
```

Persist rolling peak:
- key: `risk.rolling_peak_equity`

- [ ] **Step 4: Apply budget shrink in reduce-only band**

When mode is `REDUCE_ONLY`:
- shrink `max_gross`, `max_net`, `turnover_cap` by configured multiplier.
- emit audit event `risk.guard.drawdown_band`.

- [ ] **Step 5: Re-run tests**

Run: `./.venv/bin/pytest -q tests/test_risk_engine_utils.py`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/risk_engine/app.py tests/test_risk_engine_utils.py
git commit -m "feat(risk): enforce drawdown-band risk budget and hard halt at 5%"
```

### Task 3: Add Edge-Aware Execution Filter and Adaptive Slicing

**Files:**
- Modify: `services/execution/app.py`
- Test: `tests/test_execution_utils.py`
- Test: `tests/test_execution_planner.py`

- [ ] **Step 1: Write failing tests for edge filter**

```python
def test_should_skip_for_negative_edge():
    assert should_skip_for_edge(expected_edge_bps=1.0, est_cost_bps=2.0) is True

def test_should_execute_for_positive_edge():
    assert should_skip_for_edge(expected_edge_bps=4.0, est_cost_bps=1.5) is False
```

- [ ] **Step 2: Run test to verify failure**

Run: `./.venv/bin/pytest -q tests/test_execution_utils.py -k edge`  
Expected: FAIL with missing `should_skip_for_edge`.

- [ ] **Step 3: Implement edge filter + skip heartbeat report**

```python
def should_skip_for_edge(expected_edge_bps: float, est_cost_bps: float) -> bool:
    return expected_edge_bps <= est_cost_bps
```

On skip:
- emit audit `exec.skip_low_edge`
- emit `exec.reports` heartbeat with `skip_reason=low_edge`

- [ ] **Step 4: Implement volatility-aware slicing policy**

Add helper:

```python
def choose_slice_profile(vol_regime: float) -> tuple[int, int]:
    if vol_regime >= 0.8:
        return 5, 10
    if vol_regime <= 0.3:
        return 2, 25
    return 3, 20
```

Use profile to derive `SLICES` and `SLICE_TIMEOUT_S` per cycle.

- [ ] **Step 5: Re-run tests**

Run: `./.venv/bin/pytest -q tests/test_execution_utils.py tests/test_execution_planner.py`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/execution/app.py tests/test_execution_utils.py tests/test_execution_planner.py
git commit -m "feat(exec): add edge filter and adaptive slice profile"
```

### Task 4: Extend Reporting KPIs for Effective Trade Analysis

**Files:**
- Modify: `services/reporting/app.py`
- Test: `tests/test_reporting_utils.py`
- Modify: `scripts/hourly_trading_health_report.py`

- [ ] **Step 1: Write failing test for execution report classification**

```python
def test_exec_report_classification_low_edge_skip():
    cls = classify_exec_report({"status": "CANCELED", "raw": {"skip_reason": "low_edge"}})
    assert cls == "edge_skip"
```

- [ ] **Step 2: Run test to verify failure**

Run: `./.venv/bin/pytest -q tests/test_reporting_utils.py -k classification`  
Expected: FAIL with missing classifier.

- [ ] **Step 3: Implement classes and aggregation fields**

Classes:
- `heartbeat`
- `edge_skip`
- `risk_skip`
- `real_fill`

Update hourly report section with:
- `effective_trade_count`
- `edge_skip_count`
- `risk_skip_count`
- `heartbeat_count`

- [ ] **Step 4: Re-run tests**

Run: `./.venv/bin/pytest -q tests/test_reporting_utils.py`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/reporting/app.py tests/test_reporting_utils.py scripts/hourly_trading_health_report.py
git commit -m "feat(reporting): add effective-trade KPI decomposition"
```

### Task 5: Add Autoresearch Promotion Gate Constraints

**Files:**
- Modify: `research/autoresearch_runner.py`
- Create/Modify: `tests/research/test_promotion_orchestrator.py`

- [ ] **Step 1: Write failing tests for gate policy**

```python
def test_promotion_rejects_drawdown_breach():
    out = evaluate_candidate({"ret": 0.12, "max_drawdown": -0.061})
    assert out["action"] == "rollback"

def test_promotion_rejects_execution_regression():
    out = evaluate_candidate({"ret": 0.10, "max_drawdown": -0.03, "reject_rate_delta": 0.08})
    assert out["action"] == "rollback"
```

- [ ] **Step 2: Run test to verify failure**

Run: `./.venv/bin/pytest -q tests/research/test_promotion_orchestrator.py -k promotion`  
Expected: FAIL with missing gate checks.

- [ ] **Step 3: Implement gate checks**

Mandatory pass conditions:
- candidate return > baseline return
- `max_drawdown > -0.05`
- reject/latency/slippage regression thresholds not breached

On fail:
- action `rollback`
- reason with first breached guard

- [ ] **Step 4: Re-run tests**

Run: `./.venv/bin/pytest -q tests/research/test_promotion_orchestrator.py`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/autoresearch_runner.py tests/research/test_promotion_orchestrator.py
git commit -m "feat(research): enforce promotion gate for return uplift and dd/risk constraints"
```

### Task 6: End-to-End Verification and Rollout Notes

**Files:**
- Modify: `docs/runbook.md`
- Create: `docs/reports/profit_max_rollout_baseline_<timestamp>.md`

- [ ] **Step 1: Run focused test suite**

Run:
```bash
./.venv/bin/pytest -q \
  tests/test_ai_decision_utils.py \
  tests/test_risk_engine_utils.py \
  tests/test_execution_utils.py \
  tests/test_execution_planner.py \
  tests/test_reporting_utils.py \
  tests/research/test_promotion_orchestrator.py
```

Expected: PASS

- [ ] **Step 2: Restart local services and verify health**

Run:
```bash
./scripts/run_local.sh restart
./.venv/bin/python scripts/live_alert_check.py
./.venv/bin/python scripts/live_smoke_check.py
```

Expected:
- all services `up`
- alert/smoke result `PASS`

- [ ] **Step 3: Capture rollout baseline artifact**

Include in artifact:
- commit hash
- baseline effective trade count
- baseline 24h return
- baseline drawdown

- [ ] **Step 4: Update runbook with phased rollout and rollback triggers**

Document:
- Phase A/B/C switches
- trigger thresholds
- rollback command path

- [ ] **Step 5: Commit**

```bash
git add docs/runbook.md docs/reports/profit_max_rollout_baseline_*.md
git commit -m "docs(runbook): add profit-max rollout phases and rollback triggers"
```

## Spec Coverage Check
- Tiered signal gating and evidence: Task 1
- Drawdown guard with hard `-5%` boundary: Task 2
- Edge-aware execution and adaptive slicing: Task 3
- KPI decomposition and observability: Task 4
- Autoresearch promotion/rollback constraints: Task 5
- Phased rollout and safety verification: Task 6

## Placeholder Scan
- No `TBD`/`TODO` placeholders left.
- All tasks include exact files and runnable commands.

## Type/Interface Consistency
- New interfaces are additive:
  - decision evidence fields added in `ai_decision`
  - consumption is tolerant through dict payload extensions
  - reporting uses skip reason already present in execution raw payload
