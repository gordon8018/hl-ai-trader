# V17 Dry-Run Integration

## Purpose

This document describes the opt-in dry-run path for routing V17 shadow targets through the existing risk and execution services without changing production `alpha.target` behavior or enabling live order submission.

## Default Production Path

By default, the existing production streams remain unchanged:

- Risk consumes `alpha.target`.
- Risk emits `risk.approved`.
- Execution consumes `risk.approved`.
- Execution dead-letter input remains tied to the selected execution input stream.

No V17 shadow component writes to `alpha.target`, and the V17 bridge rejects production `alpha.target` configuration.

## V17 Dry-Run Path

Set `V17_DRY_RUN_INTEGRATION=true` only in a paper-shadow environment:

- V17 target bridge writes `alpha.target.v17_shadow`.
- Risk consumes `alpha.target.v17_shadow`.
- Risk emits `risk.approved.v17_shadow`.
- Execution consumes `risk.approved.v17_shadow`.
- Execution uses `dlq.risk.approved.v17_shadow` for the selected input stream.

Execution also requires `DRY_RUN=true` whenever `V17_DRY_RUN_INTEGRATION=true`; startup fails otherwise.

## Safety Gates

- V17 integration is opt-in and disabled by default.
- Production `alpha.target` is unchanged for non-V17 mode.
- Risk refuses V17 integration if it would consume production `alpha.target`.
- Execution refuses V17 integration unless `DRY_RUN=true`.
- No V9 production behavior, secrets, write-enabled API keys, or withdrawal credentials are introduced.
- Real exchange submission remains behind the existing `DRY_RUN` branch and is not enabled by V17 integration.

## Verification

Run the dependency-light V17 safety suite:

```bash
python -m pytest -q tests/v17/test_dryrun_integration_safety.py
```

Run the T01-T10 target suite:

```bash
python -m pytest -q tests/config/test_v17_shadow_config.py tests/v17/test_schemas.py tests/v17/test_ohlcv_cache.py tests/v17/test_feature_builder.py tests/v17/test_quality_score.py tests/v17/test_event_aggregator.py tests/v17/test_shadow_ledger.py tests/v17/test_shadow_runner.py tests/v17/test_target_bridge.py tests/v17/test_dryrun_integration_safety.py
```

When the project environment includes the optional Redis Python dependency, also rerun the existing execution checks:

```bash
python -m pytest -q tests/test_execution_planner.py tests/test_execution_control.py
```
