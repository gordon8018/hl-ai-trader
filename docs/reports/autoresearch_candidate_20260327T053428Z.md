# Autoresearch Candidate Acceptance Artifact

- profile_name: `V9_ar_20260327_053428_136049_48258afe`
- pack_dir: `docs/reports/V9_ar_20260327_053428_136049_48258afe`
- score_total: `0.8200000000000001`
- gate_pass: `true`
- gate_reasons: `[]`

## Commands Used
```bash
python3 - <<'PY'
from pathlib import Path
from research.autoresearch_runner import run_one_iteration
from research.promotion_gate import evaluate_promotion_gate

out = run_one_iteration(
    output_root=Path('docs/reports'),
    baseline_params={'AI_SIGNAL_DELTA_THRESHOLD': 0.05},
    observed_metrics={
        'daily_returns': [0.001] * 90,
        'max_drawdown': 0.18,
        'reject_rate': 0.01,
        'slippage_bps': 2.0,
    },
)
print('profile_name=', out['profile_name'])
print('pack_dir=', out['pack_dir'])
print('score_total=', out['score']['score_total'])

decision = evaluate_promotion_gate(
    candidate={
        'score_total': out['score']['score_total'] + 0.1,
        'max_drawdown': 0.16,
        'reject_rate': 0.01,
        'slippage_bps': 2.0,
    },
    baseline={
        'score_total': out['score']['score_total'],
        'max_drawdown': 0.18,
        'reject_rate': 0.01,
        'slippage_bps': 2.0,
    },
    thresholds={
        'max_dd_delta': 0.00,
        'max_reject_rate': 0.05,
        'max_slippage_bps': 8.0,
    },
)
print('gate_pass=', decision['pass'])
print('gate_reasons=', decision['reasons'])
PY
```
