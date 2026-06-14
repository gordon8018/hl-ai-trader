# V17 Live Acceptance Runbook

This runbook is an acceptance checklist only. It does not authorize real-money trading, start services, or place orders.

## Safety Gate

- Confirm `config/v17_shadow.json` has `enabled: false`, `dry_run: true`, and `real_money_execution_allowed: false`.
- Confirm `config/v17_shadow.json` has `live.enabled: false` and `live.requires_manual_confirmation: true`.
- Confirm V17 target output is isolated to `alpha.target.v17_shadow` and never the production `alpha.target` stream.
- Confirm `services/v17_alpha/app.py` imports without side effects and only exposes safe constants/helpers.

## Pre-Live Acceptance

- Run `python -m pytest -q tests/v17 tests/config/test_v17_shadow_config.py` from the V17 worktree.
- Verify portfolio state V17 reports are present and isolated before any live gate review.
- Verify state snapshot live readiness uses the shared latest state snapshot with the live guard enabled.
- Verify the dedicated V17 alpha producer returns flat/no-op targets for missing state, missing market features, and default skeleton operation.
- Verify conservative caps in `config/v17_shadow.json` are included in generated V17 target payload metadata.

## Live Gate Review

- Require a human review of the latest test output and `.run/v17-shadow/LIVE_IMPLEMENTATION_STATUS.md`.
- Require explicit manual approval before any future change that could enable live mode.
- Require separate review for any code path that writes Redis, starts a loop, calls an execution venue, or modifies production streams.
- Keep the default verdict at `IMPLEMENTATION_GATE_ADDED_NOT_LIVE` until a separate live authorization task changes it.

## Rejection Conditions

- Reject acceptance if any V17 component writes to production `alpha.target`.
- Reject acceptance if any default config enables real-money execution.
- Reject acceptance if credentials, private keys, API keys, passwords, seeds, or withdrawal settings are required or printed.
- Reject acceptance if the dedicated V17 alpha producer is missing or does more than safe flat/no-op target construction by default.
