# V17 Isolated Live Execution Completion Plan

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. Each implementation task must follow strict RED-GREEN-REFACTOR TDD and must not enable real-money execution during development.

**Goal:** Complete the remaining V17 isolated live-execution readiness work so V17 can be technically prepared for a separately approved real-money run without touching V9 production routing or starting real-money trading by default.

**Architecture:** Reuse the existing Redis Streams, `portfolio_state`, `risk_engine`, `execution`, reporting, and exchange adapter infrastructure, but keep every V17 live path isolated by explicit environment gates and V17-specific streams. The target state is a safe, auditable V17 live-capable pipeline: market data/state snapshot -> V17 alpha target -> V17 risk approval -> V17 execution -> V17 reports/state reconciliation, with real-money execution still requiring manual approval.

**Tech Stack:** Python 3.11, pytest, Redis Streams, existing `hl-ai-trader` service patterns, Hyperliquid exchange adapter, local runbook/status markdown.

---

## Scope

This plan covers the remaining work after the initial V17 live gate implementation.

Already implemented before this plan:

- `V17_LIVE_EXECUTION` / `V17_LIVE_REAL_MONEY_APPROVED` double gate in risk/execution.
- V17 live risk output stream: `risk.approved.v17_live`.
- V17 live execution report stream: `exec.reports.v17_live`.
- Default config remains dry-run/paper-shadow and real-money disabled.
- Targeted V17/config tests pass.

This plan adds:

- `portfolio_state` alignment with V17 live report stream.
- State snapshot readiness checks for live use.
- V17 alpha target path audit and missing implementation tasks.
- Acceptance gates and runbook for a future manually approved live attempt.

This plan does not authorize:

- Switching any running process to real-money mode.
- Setting `DRY_RUN=false` outside a controlled future manual approval.
- Placing trades.
- Storing secrets, exchange private keys, or write-enabled credentials.

---

## Non-Negotiable Safety Rules

- V17 live capability must be opt-in only.
- Default config must keep `dry_run: true` and `real_money_execution_allowed: false`.
- No task may write V17 output to production `alpha.target`.
- No task may make V17 consume production `risk.approved` in live mode.
- No task may use default `exec.reports` for V17 live execution reports.
- No task may log or store private keys, API secrets, seed phrases, passwords, or connection credentials.
- All tests must prove live behavior is gated and dry-run remains safe.
- Any eventual real-money run requires explicit separate manual confirmation from Gordon.

---

## Canonical Paths

- Worktree: `/Users/gordon8018/workspace/.worktrees/hl-ai-trader/v17-shadow-integration`
- Config: `config/v17_shadow.json`
- Risk engine: `services/risk_engine/app.py`
- Execution engine: `services/execution/app.py`
- Portfolio state: `services/portfolio_state/app.py`
- V17 readiness tests: `tests/v17/test_live_execution_readiness.py`
- Dry-run safety tests: `tests/v17/test_dryrun_integration_safety.py`
- Config tests: `tests/config/test_v17_shadow_config.py`
- Status document: `.run/v17-shadow/LIVE_IMPLEMENTATION_STATUS.md`
- Plan document: `docs/plans/2026-06-14-v17-isolated-live-execution-completion.md`

---

## Target Stream Contract

### Existing / Production Streams

These remain production/default streams and must not be used by V17 live mode except where explicitly stated as shared read-only infrastructure:

- `alpha.target`
- `risk.approved`
- `exec.reports`
- `latest.state.snapshot`
- `state.snapshot`
- `state.events`
- `audit.logs`

### V17 Isolated Streams

V17 live-capable mode must use:

- V17 target input to risk: `alpha.target.v17_shadow`
- V17 risk approval output: `risk.approved.v17_live`
- V17 execution report output: `exec.reports.v17_live`

### Shared State Key

`latest.state.snapshot` can remain shared only if it is proven to be fresh, reconciled, and sourced from the intended account. If account separation is required later, introduce a V17-specific state key such as `latest.state.snapshot.v17_live` in a separate task.

**Decision:** V17 live continues to use shared `latest.state.snapshot` for now, protected by the V17 live readiness guard that fails closed unless the snapshot is fresh and `health.reconcile_ok` is true. Introduce `latest.state.snapshot.v17_live` only if V17 live later uses an independent account, or if V9 and V17 must run concurrently against different accounts.

---

## Definition of Done

The development task is complete only when all of the following are true:

- `portfolio_state` consumes `exec.reports.v17_live` when `V17_LIVE_EXECUTION=true`.
- `portfolio_state` keeps default/dry-run behavior unchanged when V17 live is disabled.
- Tests prove V17 live reports are isolated end-to-end across execution and state reconciliation.
- Tests prove live gates fail closed when approval is missing or `DRY_RUN=true`.
- V17 alpha target path is documented and implemented or explicitly marked blocked with exact missing dependency.
- Targeted tests pass: `pytest -q tests/v17 tests/config/test_v17_shadow_config.py`.
- No test or implementation stores secrets.
- Status/runbook documents clearly say real-money trading is not enabled by these changes.

---

# Task Plan

## Task 1: Finish Portfolio State V17 Report Isolation

**Objective:** Make `portfolio_state` consume V17 live execution reports from `exec.reports.v17_live` when V17 live mode is explicitly enabled.

**Files:**

- Modify: `services/portfolio_state/app.py`
- Modify: `tests/v17/test_live_execution_readiness.py`

**Step 1: Write failing AST test**

Add a test similar to:

```python
def test_portfolio_state_v17_live_reports_are_isolated_from_default_reports() -> None:
    tree = _module_tree(PORTFOLIO_STATE_APP)

    stream_reports = _assign_expr(tree, "STREAM_REPORTS")
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_reports)
    assert "exec.reports.v17_live" in _constant_strings(stream_reports)
    assert "exec.reports" in _constant_strings(stream_reports)
```

**Step 2: Verify RED**

Run:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17/test_live_execution_readiness.py::test_portfolio_state_v17_live_reports_are_isolated_from_default_reports
```

Expected: fail because `services/portfolio_state/app.py` still assigns `STREAM_REPORTS = "exec.reports"` directly.

**Step 3: Implement minimal routing**

In `services/portfolio_state/app.py`, add environment parsing near existing constants:

```python
V17_LIVE_EXECUTION = os.environ.get("V17_LIVE_EXECUTION", "false").lower() == "true"
STREAM_REPORTS = "exec.reports.v17_live" if V17_LIVE_EXECUTION else "exec.reports"
```

Keep `GROUP_REPORTS` unchanged unless tests prove consumer group collision or offset reuse is unsafe.

**Step 4: Verify GREEN**

Run the single test, then:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17/test_live_execution_readiness.py
```

Expected: pass.

**Step 5: Regression check**

Run:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17 tests/config/test_v17_shadow_config.py
```

Expected: pass.

---

## Task 2: Add Portfolio State Import Gate Tests

**Objective:** Prove `portfolio_state` imports safely under default and V17 live environment combinations without requiring real-money execution.

**Files:**

- Modify: `tests/v17/test_live_execution_readiness.py`

**Step 1: Write failing/behavioral subprocess tests**

Add subprocess tests that import `services.portfolio_state.app` with controlled environment:

- default: `DRY_RUN=true`, `V17_LIVE_EXECUTION` unset
- V17 live: `V17_LIVE_EXECUTION=true`, `DRY_RUN=false`, no private key values printed

Use a helper that redacts environment output and only asserts module constants.

**Step 2: Verify RED or baseline**

Run the new tests. If they pass immediately, confirm they are testing the newly introduced `STREAM_REPORTS` live branch and not merely importing the module.

**Step 3: Adjust implementation only if needed**

If import fails due unrelated required env vars, set safe dummy env values in the subprocess test only:

- `REDIS_URL=redis://127.0.0.1:6381/0`
- `HL_ACCOUNT_ADDRESS=0x0000000000000000000000000000000000000000`
- `DRY_RUN=true` for default case

Never set or print real secrets.

**Step 4: Verify**

Run:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17/test_live_execution_readiness.py
```

Expected: pass.

---

## Task 3: Audit State Snapshot Freshness and Account Safety

**Objective:** Add a testable readiness check that prevents execution from using missing, stale, or unhealthy `latest.state.snapshot` in V17 live mode.

**Files:**

- Modify: `services/execution/app.py`
- Modify: `tests/v17/test_live_execution_readiness.py` or create `tests/v17/test_live_state_readiness.py`

**Step 1: Write failing unit test for stale state**

Test desired behavior:

- if `V17_LIVE_EXECUTION=true`
- and `latest.state.snapshot` is missing
- execution must not produce orders and must route to audit/DLQ or skip safely

If direct unit testing is hard, first extract a small pure helper from `services/execution/app.py`:

```python
def validate_live_state_snapshot(st: StateSnapshot | None, *, max_age_seconds: int) -> tuple[bool, str]:
    ...
```

**Step 2: Verify RED**

Run the specific test and confirm failure is because the helper does not exist or stale state is accepted.

**Step 3: Implement minimal helper**

Helper requirements:

- reject `None`
- reject `health.reconcile_ok is False`
- reject missing `last_reconcile_ts`
- reject stale timestamps if timestamp parsing is available
- return a machine-readable reason string

**Step 4: Wire helper into execution live path**

Only enforce stricter rejection when `V17_LIVE_EXECUTION=true`. Do not break existing dry-run behavior unless tests require it.

**Step 5: Verify**

Run:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17/test_live_state_readiness.py tests/v17/test_live_execution_readiness.py
```

Expected: pass.

---

## Task 4: Decide Shared vs V17-Specific State Key

**Objective:** Make an explicit architectural decision about whether V17 live may use shared `latest.state.snapshot` or needs `latest.state.snapshot.v17_live`.

**Files:**

- Modify: `docs/plans/2026-06-14-v17-isolated-live-execution-completion.md`
- Modify: `.run/v17-shadow/LIVE_IMPLEMENTATION_STATUS.md`
- Optionally modify: `config/v17_shadow.json`
- Optionally modify: `services/portfolio_state/app.py`
- Optionally modify: `services/execution/app.py`

**Decision Criteria:**

Use shared `latest.state.snapshot` only if:

- the same account is intentionally used for V17 live execution;
- snapshot health and freshness are enforced before orders;
- the account address is not logged as a secret and is not a private key;
- production and V17 live are not expected to run against different accounts simultaneously.

Introduce `latest.state.snapshot.v17_live` if:

- V17 live uses a separate account;
- production V9 and V17 may run simultaneously;
- report/state reconciliation should be fully separated;
- a future rollback requires leaving production state untouched.

**Current Decision:** Keep using shared `latest.state.snapshot` for V17 live because no independent V17 account requirement has been provided. The Task 3 live readiness guard is mandatory on the V17 live path and must fail closed for missing, stale, unhealthy, or unreconciled snapshots before any live execution proceeds.

**Future Trigger:** Add `latest.state.snapshot.v17_live` in a separate task if V17 live receives an independent account, if V9 and V17 need to run simultaneously against different accounts, or if rollback/reconciliation requirements demand fully separated state writes and reads.

**Implementation if V17-specific key is chosen:**

Add config:

```json
"state": {
  "latest_key": "latest.state.snapshot",
  "live_latest_key": "latest.state.snapshot.v17_live"
}
```

Then route both `portfolio_state` write and `execution` read based on `V17_LIVE_EXECUTION`.

**Verification:**

Add AST/config tests proving the selected key appears in config and services.

---

## Task 5: Audit Existing Alpha Target Path

**Objective:** Identify whether an existing service can produce `alpha.target.v17_shadow` from live market/state inputs, or whether a dedicated V17 alpha producer is required.

**Files:**

- Inspect: `services/ai_decision/app.py`
- Inspect: `services/risk_engine/app.py`
- Inspect: `docs/services/ai_decision.md`
- Inspect: `docs/services/risk_engine.md`
- Inspect: `config/v17_shadow.json`
- Create or modify tests under `tests/v17/`

**Step 1: Search current producers**

Run:

```bash
rg "alpha.target|alpha.target.v17_shadow|STREAM_OUT|TargetPortfolio|ApprovedTargetPortfolio" services shared tests docs config
```

**Step 2: Document result**

Write one of these conclusions into `.run/v17-shadow/LIVE_IMPLEMENTATION_STATUS.md`:

- `alpha_target_path: existing_producer_can_be_v17_routed`
- `alpha_target_path: dedicated_v17_producer_required`
- `alpha_target_path: blocked_missing_market_or_state_dependency`

**Step 3: Add test capturing desired route**

If existing producer can be routed, write a test proving `V17_DRY_RUN_INTEGRATION` or `V17_LIVE_EXECUTION` outputs `alpha.target.v17_shadow` instead of `alpha.target`.

If dedicated producer is needed, create a failing test for the new producer module path, e.g.:

```python
def test_v17_alpha_live_target_stream_is_isolated() -> None:
    tree = _module_tree(PROJECT_ROOT / "services" / "v17_alpha" / "app.py")
    assert _constant_value(tree, "STREAM_OUT") == "alpha.target.v17_shadow"
```

**Step 4: Verify RED**

Run the focused test and confirm it fails for the expected reason.

---

## Task 6: Implement V17 Alpha Target Producer Skeleton

**Objective:** Create a minimal V17 alpha target service that can safely publish zero/flat or candidate V17 targets to `alpha.target.v17_shadow` without touching production `alpha.target`.

**Files:**

- Create: `services/v17_alpha/app.py`
- Create: `services/v17_alpha/__init__.py`
- Create: `tests/v17/test_v17_alpha_target.py`
- Modify: `config/v17_shadow.json`

**Step 1: Write failing tests**

Tests must cover:

- output stream is exactly `alpha.target.v17_shadow`
- no production `alpha.target` string appears as an output constant
- missing/stale state produces no target or flat target with reason
- missing/stale market features produces no target or flat target with reason
- generated target respects configured caps

**Step 2: Verify RED**

Run:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17/test_v17_alpha_target.py
```

Expected: fail because module does not exist.

**Step 3: Implement minimal service**

Minimal skeleton requirements:

- Read config from `config/v17_shadow.json`.
- Read latest market features from configured V17 feature stream or existing `md.features.*` only if documented.
- Read `latest.state.snapshot` or selected V17 state key.
- Write only `alpha.target.v17_shadow`.
- Default output should be flat/no-op until signal logic is explicitly validated.
- Include clear reasons in payload metadata.

**Step 4: Verify GREEN**

Run the focused test and then V17/config suite.

---

## Task 7: Add Live Acceptance Checklist

**Objective:** Define objective gates that must pass before any future real-money run can be manually approved.

**Files:**

- Modify: `.run/v17-shadow/LIVE_IMPLEMENTATION_STATUS.md`
- Create: `docs/runbooks/v17_live_acceptance.md` or add to existing `docs/runbook.md`

**Required Gates:**

- Targeted tests pass.
- Import matrix passes for default, V17 dry-run, V17 live unapproved, and V17 live approved-with-dry-run-blocked.
- Redis stream lengths show no accidental production output:
  - `alpha.target` remains unchanged during V17-only run.
  - `risk.approved` remains unchanged during V17-only run.
  - `exec.orders` remains zero unless explicitly running future approved live test.
- `alpha.target.v17_shadow` emits expected targets or documented flat/no-op targets.
- `risk.approved.v17_live` emits only after risk checks pass.
- `exec.reports.v17_live` is isolated from `exec.reports`.
- `latest.state.snapshot` is fresh and reconcile healthy.
- No secrets are present in logs, docs, tests, or git diff.

**Verification:**

Run:

```bash
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17 tests/config/test_v17_shadow_config.py
```

Expected: pass.

---

## Task 8: Update Status and Handoff Notes

**Objective:** Leave the repository in a state where the next agent/developer can continue without guessing.

**Files:**

- Modify: `.run/v17-shadow/LIVE_IMPLEMENTATION_STATUS.md`
- Modify: `docs/plans/2026-06-14-v17-isolated-live-execution-completion.md`

**Status Fields to Maintain:**

```yaml
verdict: IMPLEMENTATION_GATE_ADDED_NOT_LIVE
real_money_started: false
dry_run_runtime_kept: true
portfolio_state_v17_reports: pending|implemented|verified
state_snapshot_live_readiness: pending|implemented|verified
alpha_target_path: pending|existing_producer_can_be_v17_routed|dedicated_v17_producer_required|blocked_missing_dependency
acceptance_runbook: pending|implemented
```

**Verification:**

Before handoff, run:

```bash
git status --short
/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m pytest -q tests/v17 tests/config/test_v17_shadow_config.py
```

Expected:

- Git status shows only intended files.
- Tests pass.
- Status file states real-money was not started.

---

## Suggested Execution Order

1. Task 1: `portfolio_state` report stream isolation.
2. Task 2: portfolio import/env matrix tests.
3. Task 3: live state freshness/readiness guard.
4. Task 4: shared vs V17-specific state key decision.
5. Task 5: alpha target producer audit.
6. Task 6: V17 alpha target producer skeleton if needed.
7. Task 7: live acceptance checklist.
8. Task 8: status and handoff notes.

Do not start Task 6 until Task 5 determines whether a new producer is actually required.

---

## Final Safety Statement

Completing this plan should produce a V17 live-capable, isolated, tested implementation. It should not place trades and should not enable real-money trading by default. The final transition from implementation readiness to real-money operation remains a separate manual decision requiring explicit confirmation from Gordon.
