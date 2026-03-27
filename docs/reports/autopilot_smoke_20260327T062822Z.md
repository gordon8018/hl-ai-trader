# Autopilot Smoke Verification Artifact

## Commands Run
1. `pytest -q tests/research/test_deployment_state.py tests/research/test_promotion_orchestrator.py tests/scripts/test_show_current_version.py`
2. `pytest -q tests/research/test_strategy_score.py tests/research/test_candidate_pack.py tests/research/test_promotion_gate.py`
3. `python3 scripts/show_current_version.py --payload "{\"version\": \"V9_prod\", \"stage\": \"stable\", \"promoted_at\": \"2026-03-27T00:00:00Z\", \"previous_version\": \"V9_recovery\", \"reason\": \"manual promotion\"}"`
4. `python3 - <<'PY'
from research.promotion_orchestrator import evaluate_and_decide

promote = evaluate_and_decide(
    metrics={"drawdown": 0.12, "reject_rate": 0.01, "slippage_bps": 2.0},
    thresholds={
        "max_drawdown": 0.20,
        "max_reject_rate": 0.05,
        "max_slippage_bps": 8.0,
    },
)
rollback = evaluate_and_decide(
    metrics={"drawdown": 0.25, "reject_rate": 0.10, "slippage_bps": 12.0},
    thresholds={
        "max_drawdown": 0.20,
        "max_reject_rate": 0.05,
        "max_slippage_bps": 8.0,
    },
)

print(promote)
print(rollback)
PY`

## Test Summary
- deployment / orchestrator / script tests: passed
- research scoring / candidate pack / promotion gate tests: passed

## Current Version Visibility Example
```text
version=V9_prod
stage=stable
promoted_at=2026-03-27T00:00:00Z
previous_version=V9_recovery
reason=manual promotion
```

## Gate Samples
### Promote Sample
```json
{
  "action": "promote",
  "reason": "gate_pass"
}
```

### Rollback Sample
```json
{
  "action": "rollback",
  "reason": "drawdown_breach"
}
```

## Key Config Knobs
- `active_version`: `V9_prod`
- `AUTO_EVOLVE_ENABLED`: `false`
- `AUTO_SHADOW_HOURS`: `24`
- `AUTO_CANARY_MAX_GROSS`: `0.1`
- `AUTO_GATE_MAX_DRAWDOWN`: `0.2`
- `AUTO_GATE_MAX_REJECT_RATE`: `0.05`
- `AUTO_GATE_MAX_SLIPPAGE_BPS`: `8.0`
