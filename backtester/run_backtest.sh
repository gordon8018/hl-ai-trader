#!/usr/bin/env bash
# backtester/run_backtest.sh
# Usage:
#   ./backtester/run_backtest.sh                        # 7-day dry-run
#   ./backtester/run_backtest.sh --days 14              # 14-day dry-run
#   ./backtester/run_backtest.sh --outcome-horizon 30   # 30-min outcome window
#   ./backtester/run_backtest.sh --compare 3 14         # compare last 3 days vs last 14 days
#   NOTE: --use-llm is not yet implemented (reserved for future use)

set -euo pipefail
cd "$(dirname "$0")/.."

DAYS=7
USE_LLM=false
OUTCOME_HORIZON=60
COMPARE_DAYS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --days)            DAYS="$2";            shift 2 ;;
        --use-llm)         echo "ERROR: --use-llm is not yet implemented. LLM replay mode is reserved for a future release." >&2; exit 1 ;;
        --outcome-horizon) OUTCOME_HORIZON="$2"; shift 2 ;;
        --compare)         COMPARE_DAYS="$2 $3"; shift 3 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Load env if available
if [[ -f infra/.env ]]; then
    set -a
    source infra/.env
    set +a
fi

export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

echo "Running backtest: days=${DAYS} use_llm=${USE_LLM} outcome_horizon=${OUTCOME_HORIZON}min"

python3 - <<EOF
import sys
sys.path.insert(0, '.')
from backtester.replay import run_replay
from backtester.evaluator import evaluate, print_report

compare_days = "${COMPARE_DAYS}".strip()
if compare_days:
    parts = compare_days.split()
    days_a, days_b = int(parts[0]), int(parts[1])
    print(f"=== Comparing last {days_a} days vs last {days_b} days ===")
    for label, d in [(f"Last {days_a}d", days_a), (f"Last {days_b}d", days_b)]:
        preds = run_replay(days=d, use_llm=${USE_LLM,,}, outcome_horizon_min=${OUTCOME_HORIZON})
        results = evaluate(preds)
        print(f"\n--- {label} ---")
        print_report(results)
else:
    predictions = run_replay(
        days=${DAYS},
        use_llm=${USE_LLM,,},
        outcome_horizon_min=${OUTCOME_HORIZON},
    )
    results = evaluate(predictions)
    print_report(results)
EOF
