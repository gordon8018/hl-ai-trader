# V17 Shadow Trading System Readiness Report

## Status

V17 shadow trading is ready for manual integration packaging and a paper/live-shadow observation decision. It is not enabled for real-money execution.

## Implemented Files

### Config and documentation

- `config/v17_shadow.json` — safe default V17 paper-shadow configuration.
- `docs/plans/v17-shadow-trading-system.md` — task plan and acceptance gates.
- `docs/v17_dryrun_integration.md` — dry-run stream isolation and verification notes.
- `docs/v17_shadow_readiness_report.md` — this readiness report.

### Shared V17 data contracts

- `shared/v17/__init__.py`
- `shared/v17/schemas.py`

### Data builder and signal engine

- `services/v17_data_builder/__init__.py`
- `services/v17_data_builder/ohlcv_cache.py`
- `services/v17_data_builder/features.py`
- `services/v17_signal_engine/__init__.py`
- `services/v17_signal_engine/quality.py`
- `services/v17_signal_engine/event_aggregator.py`

### Shadow pipeline

- `services/v17_shadow/__init__.py`
- `services/v17_shadow/ledger.py`
- `services/v17_shadow/runner.py`
- `services/v17_shadow/target_bridge.py`
- `services/v17_shadow/reporting.py`

### Dry-run risk/execution integration

- `services/risk_engine/app.py` — opt-in V17 shadow stream routing guarded by `V17_DRY_RUN_INTEGRATION`.
- `services/execution/app.py` — opt-in V17 dry-run execution routing guarded by `V17_DRY_RUN_INTEGRATION` and `DRY_RUN=true`.

### Tests and fixtures

- `tests/config/test_v17_shadow_config.py`
- `tests/fixtures/v17/multi_symbol_1h.csv`
- `tests/v17/test_schemas.py`
- `tests/v17/test_ohlcv_cache.py`
- `tests/v17/test_feature_builder.py`
- `tests/v17/test_quality_score.py`
- `tests/v17/test_event_aggregator.py`
- `tests/v17/test_shadow_ledger.py`
- `tests/v17/test_shadow_runner.py`
- `tests/v17/test_target_bridge.py`
- `tests/v17/test_dryrun_integration_safety.py`
- `tests/v17/test_shadow_reporting.py`
- `tests/v17/test_full_replay_pipeline.py`

## Test Commands and Results

### Passed

```bash
python -m pytest -q tests/v17 tests/config/test_v17_shadow_config.py
```

Result: passed, 73 tests.

```bash
pytest -q tests/v17/test_full_replay_pipeline.py && pytest -q tests/v17
```

Result: passed during T12 reviewer verification. Output included `.. [100%]` for replay and `................................................................... [100%]` for `tests/v17`.

### Blocked by local environment

```bash
python -m pytest -q tests/test_execution_planner.py tests/test_execution_control.py
```

Result: blocked/failing in this environment because `shared/bus/redis_streams.py` imports `redis`, and the local Python environment does not have the `redis` package installed.

Observed error:

```text
ModuleNotFoundError: No module named 'redis'
```

This is an environment dependency blocker for the existing execution tests, not evidence that V17 enabled live execution. T10 reviewer previously verified the V17 dry-run integration safety surface with dependency-light tests.

## Safety Confirmation

- V17 defaults are disabled/paper-shadow/dry-run only.
- `config/v17_shadow.json` keeps `enabled=false`, `mode=paper_shadow`, `dry_run=true`, and `real_money_execution_allowed=false`.
- V17 target stream is isolated to `alpha.target.v17_shadow`.
- Production `alpha.target` is rejected by V17 runner/bridge validation tests.
- Execution requires `DRY_RUN=true` when `V17_DRY_RUN_INTEGRATION=true`.
- V17 dry-run execution uses the isolated `risk.approved.v17_shadow` stream and avoids exchange adapter construction in V17 dry-run mode.
- No seed phrases, private keys, withdrawal credentials, write-enabled API keys, or other secrets were added.
- No automatic promotion to canary, V9/V9_prod replacement, or real-money execution is included.

## Known Limitations

- Most V17 files are currently untracked in the shared integration worktree. Final packaging must explicitly add/include the intended V17 files before PR or merge.
- Full repository `pytest -q` was not run as part of this readiness pass.
- Existing execution tests could not be completed without installing the local `redis` Python package.
- T12 replay coverage is deterministic and safety-focused but does not run live Redis, risk, or execution services end-to-end.
- No production deployment, scheduling, or automatic Discord/Ops relay wiring is included.

## Manual Decision Needed

Manual Ops decision: whether to run V17 in paper/live-shadow observation mode for 2-4 weeks.

Recommended conditions before starting observation:

1. Package all intended V17 files into the branch/PR and verify `git status` has no accidental omissions.
2. Re-run V17 targeted tests after packaging.
3. Re-run existing execution tests in an environment with `redis` installed, or document that they remain an environment-blocked gate.
4. Keep `V17_DRY_RUN_INTEGRATION=false` unless explicitly enabling paper/live-shadow routing.
5. Keep `DRY_RUN=true` and `real_money_execution_allowed=false`; do not enable any real-money path without a separate explicit approval.
