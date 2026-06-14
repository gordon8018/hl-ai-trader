# V17 Shadow Trading System Implementation Plan

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. Keep every task in an isolated git worktree and route through Developer → Tester → Reviewer gates.

**Goal:** Build a V17 paper/live-shadow trading subsystem on top of `hl-ai-trader` without modifying the existing V9 production strategy path or enabling real-money execution.

**Architecture:** Keep the existing Redis Streams, risk engine, execution engine, portfolio state, reporting, and Hyperliquid adapter as reusable infrastructure. Add an isolated V17 data/signals/shadow/bridge subsystem that writes only V17-specific streams and ledgers until explicitly promoted. Promotion to dry-run integration is allowed only through a dedicated V17 bridge stream; real-money trading remains out of scope.

**Tech Stack:** Python 3.11, pytest, Redis Streams, existing `hl-ai-trader` schemas and service patterns, local CSV/SQLite ledgers.

---

## Non-Negotiable Safety Rules

- Do not modify `V9_prod` behavior or production strategy configuration.
- Do not write to the production `alpha.target` stream from V17 tasks.
- Do not enable real-money execution, private keys, write-enabled API keys, withdrawals, or automatic submit paths.
- Default all V17 integration to `paper/live-shadow` and `DRY_RUN=true`.
- Do not store secrets in code, docs, tests, task cards, logs, or ledgers.
- Treat all performance outputs as historical/paper research only, not live trade recommendations.

## Canonical Paths

- Project root: `/Users/gordon8018/workspace/hl-ai-trader`
- Worktree base: `/Users/gordon8018/workspace/.worktrees/hl-ai-trader`
- Plan file: `docs/plans/v17-shadow-trading-system.md`
- Task queue: `.hermes/team/pipeline/tasks/pending/`
- Handoff folder: `.hermes/team/pipeline/handoff/`
- V17 config target: `config/v17_shadow.json`

## Proposed V17 Streams

- `v17.features.1h`
- `v17.signals`
- `v17.shadow.ledger`
- `alpha.target.v17_shadow`

These names are intentionally separate from production streams. Any later stream name change must preserve isolation from production `alpha.target`.

## Stage Gates

### 1. Spec Gate

Reviewer confirms the task matches this plan and the V17 paper/live-shadow specification. The implementation must not mix in V9 decision logic unless explicitly required for compatibility.

### 2. Test Gate

Tester confirms targeted tests pass. For tasks involving signal generation, tests must include no-future-data checks, deterministic event IDs, duplicate guards, and cap behavior.

### 3. Safety Gate

Reviewer confirms no production stream writes, no real submit calls, no secrets, and no live execution enablement.

### 4. Integration Gate

Final reviewer confirms the full V17 shadow chain can be replayed and reconciled: features → signal → ledger → optional dry-run target → report.

---

## Task Dependency Graph

```text
T01 ─┐
     ├─> T03 ─> T04 ─> T05 ─> T06 ─> T07 ─> T08 ─> T09 ─> T10 ─> T11 ─> T12
T02 ─┘
```

T01 and T02 may run in parallel if they do not touch the same files. T03 and later should be sequential until the first stable integration exists.

---

## Task T01: Add V17 Shadow Configuration

**Objective:** Add a dedicated V17 shadow configuration file with safe defaults and no production side effects.

**Files:**
- Create: `config/v17_shadow.json`
- Test: `tests/config/test_v17_shadow_config.py`

**Requirements:**
- Include `enabled: false` by default.
- Include stream names for `v17.features.1h`, `v17.signals`, `v17.shadow.ledger`, and `alpha.target.v17_shadow`.
- Include `mode: "paper_shadow"` and `dry_run: true`.
- Include conservative caps separate from existing `V9_prod` parameters.
- Include freshness tolerance for completed 1H bars.
- Include ledger path under local project data, not Obsidian.
- Include explicit `real_money_execution_allowed: false`.

**Verification:**
- Run targeted config test.
- Confirm no existing production config file is modified.

---

## Task T02: Define V17 Schemas

**Objective:** Define typed V17 event/signal/ledger payloads while keeping bridge compatibility with existing `TargetPortfolio`.

**Files:**
- Create or modify: `shared/v17/schemas.py`
- Create: `shared/v17/__init__.py`
- Test: `tests/v17/test_schemas.py`

**Requirements:**
- Define `V17FeatureSnapshot`, `V17SignalEvent`, `V17SignalLeg`, and `V17LedgerRow`.
- Include fields for `event_id`, `entry_time_utc`, `bar_close_time_utc`, `symbol`, `side`, `quality_score`, `quality_label`, `event_gross_cap`, `raw_leg_weight`, `final_leg_weight`, `status`, and `reason`.
- Support invalid/blocked statuses without raising unless the payload is structurally malformed.
- Preserve serializable dict conversion for Redis/ledger use.

**Verification:**
- Unit tests cover serialization, required fields, invalid status rows, and deterministic roundtrip.

---

## Task T03: Build 1H OHLCV Cache Reader

**Objective:** Implement a V17 data-access layer that reads completed 1H OHLCV bars and rejects incomplete/stale bars.

**Files:**
- Create: `services/v17_data_builder/ohlcv_cache.py`
- Create: `services/v17_data_builder/__init__.py`
- Test: `tests/v17/test_ohlcv_cache.py`

**Requirements:**
- Return completed 1H bars only.
- Expose at least timestamp, open, high, low, close, volume, and symbol.
- Reject or mark stale bars based on config freshness tolerance.
- Handle missing symbols and insufficient history explicitly.
- Do not depend on current `market_data` mid-price approximations for V17 scoring.

**Verification:**
- Tests cover completed bar filtering, stale bar rejection, missing symbol behavior, and insufficient history behavior.

---

## Task T04: Implement V17 Feature Builder

**Objective:** Compute V17-required features from completed 1H OHLCV windows.

**Files:**
- Create: `services/v17_data_builder/features.py`
- Test: `tests/v17/test_feature_builder.py`

**Requirements:**
- Compute ATR or ATR proxy from OHLCV.
- Compute bar range and range rank over a rolling lookback.
- Compute volume rank over a rolling lookback.
- Compute side-aware momentum rank.
- Require enough lookback bars before producing valid features.
- Avoid future data by using only bars at or before the completed signal bar.

**Verification:**
- Tests include deterministic fixtures and a no-future-data check.

---

## Task T05: Implement V17 Quality Score

**Objective:** Implement frozen V17 quality scoring and quality labels.

**Files:**
- Create: `services/v17_signal_engine/quality.py`
- Create: `services/v17_signal_engine/__init__.py`
- Test: `tests/v17/test_quality_score.py`

**Requirements:**
- Implement quality score as a pure deterministic function.
- Return score plus label bucket, e.g. Q1/Q2/Q3/Q4 or configured equivalents.
- Return invalid/score-unavailable status when required fields are missing.
- Do not call LLMs, exchange APIs, Redis, or execution services.
- Keep thresholds configurable through V17 shadow config but default to frozen V17 values.

**Verification:**
- Tests cover threshold boundaries, missing fields, stable output, and label-to-cap mapping.

---

## Task T06: Implement Event Aggregator and Cap Logic

**Objective:** Aggregate same-timestamp multi-symbol signals into event-level exposure with gross cap enforcement.

**Files:**
- Create: `services/v17_signal_engine/events.py`
- Test: `tests/v17/test_event_aggregator.py`

**Requirements:**
- Group legs by event timestamp and strategy version.
- Generate deterministic `event_id`.
- Compute raw gross and final gross.
- Scale legs proportionally when raw gross exceeds event cap.
- Preserve long/short side and symbol-level signs.
- Never exceed event gross cap after rounding.

**Verification:**
- Tests cover single-leg, multi-leg, long/short, cap scaling, rounding, and deterministic event IDs.

---

## Task T07: Implement V17 Shadow Ledger

**Objective:** Write an auditable V17 paper/live-shadow ledger with duplicate protection.

**Files:**
- Create: `services/v17_shadow/ledger.py`
- Create: `services/v17_shadow/__init__.py`
- Test: `tests/v17/test_shadow_ledger.py`

**Requirements:**
- Support append-only CSV or SQLite; choose the simpler approach consistent with project style.
- Reject duplicate `event_id + leg_id` writes or mark them as duplicates without double-counting.
- Record valid, invalid, blocked, and duplicate rows.
- Include enough columns to reconstruct signal decisions.
- Avoid storing secrets or raw credentials.

**Verification:**
- Tests cover append, duplicate guard, invalid status rows, and ledger readback.

---

## Task T08: Build V17 Shadow Runner

**Objective:** Orchestrate data → features → signals → event aggregation → ledger in shadow-only mode.

**Files:**
- Create: `services/v17_shadow/runner.py`
- Test: `tests/v17/test_shadow_runner.py`

**Requirements:**
- Read V17 shadow config.
- Process completed bars only.
- Write V17 signals and ledger rows only to V17-specific destinations.
- Never call execution submit or production `alpha.target`.
- Emit clear skip reasons: stale data, insufficient history, missing score, duplicate event, blocked by config.

**Verification:**
- Tests use fake data and fake ledger; verify no production stream names are used.

---

## Task T09: Add V17 Target Bridge

**Objective:** Convert approved V17 shadow events into existing `TargetPortfolio`-compatible payloads on an isolated V17 bridge stream.

**Files:**
- Create: `services/v17_shadow/target_bridge.py`
- Test: `tests/v17/test_target_bridge.py`

**Requirements:**
- Read only valid V17 event rows.
- Produce target weights compatible with existing `TargetPortfolio` schema.
- Include metadata identifying `model.name = "v17"` and `model.mode = "paper_shadow"`.
- Write only to `alpha.target.v17_shadow`.
- Respect V17 config caps before producing targets.

**Verification:**
- Tests confirm schema compatibility and stream isolation.

---

## Task T10: Integrate with Risk/Execution Dry Run

**Objective:** Wire the V17 bridge into existing risk/execution dry-run paths without touching live submit behavior.

**Files:**
- Modify only if necessary: risk/execution service config or adapter wiring files.
- Prefer create: `docs/v17_dryrun_integration.md`
- Test: `tests/v17/test_dryrun_integration_safety.py`

**Requirements:**
- Demonstrate how `alpha.target.v17_shadow` is consumed in dry-run mode.
- Do not enable live execution.
- Do not modify production `alpha.target` behavior.
- Add a safety test that fails if V17 config points to production stream names.

**Verification:**
- Targeted safety tests pass.
- Reviewer confirms no live submit path can be reached from V17 bridge by default.

---

## Task T11: Add V17 Shadow Reporting

**Objective:** Produce concise daily V17 shadow reports for Ops review.

**Files:**
- Create: `services/v17_shadow/reporting.py`
- Test: `tests/v17/test_shadow_reporting.py`

**Requirements:**
- Summarize signal count, valid count, invalid count, blocked count, duplicates, per-symbol exposure, and paper PnL if available.
- Include data freshness anomalies and missing-history symbols.
- Include warning if dry-run bridge is disabled or if no events were processed.
- Keep output concise and suitable for Discord/Ops relay.

**Verification:**
- Tests cover normal day, no-signal day, anomaly day, and duplicate rows.

---

## Task T12: Full Replay and Integration Tests

**Objective:** Add historical fixture replay tests proving the V17 shadow pipeline is deterministic and safe.

**Files:**
- Create: `tests/v17/test_full_replay_pipeline.py`
- Create fixture data under an existing test fixture directory, or create: `tests/fixtures/v17/`

**Requirements:**
- Run a small multi-symbol 1H fixture through data/features/quality/events/ledger.
- Verify event IDs are deterministic.
- Verify event cap never exceeded.
- Verify duplicate replay does not double-count.
- Verify bridge writes only to `alpha.target.v17_shadow`.
- Verify no real exchange submit function is called.

**Verification:**
- Targeted replay test passes.
- Final integration reviewer approves safety and replay determinism.

---

## Final Handoff Requirements

After all tasks complete:

1. Run targeted V17 test suite.
2. Run existing risk/execution tests if touched.
3. Produce `docs/v17_shadow_readiness_report.md` with:
   - Implemented files.
   - Test commands and results.
   - Known limitations.
   - Confirmation that no live execution is enabled.
   - Next manual decision: whether to run shadow mode for 2-4 weeks.

## Out of Scope

- Real-money execution.
- Private key or write-enabled API-key setup.
- Automatic promotion to canary.
- Strategy parameter optimization.
- Replacing V9/V9_prod.
- Discord automation beyond concise Ops status summaries.
