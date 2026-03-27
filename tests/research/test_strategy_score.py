from research.strategy_score import evaluate_candidate


def test_score_prefers_better_return_and_lower_drawdown():
    base = {
        "daily_returns": [0.001] * 90,
        "max_drawdown": 0.20,
        "reject_rate": 0.01,
        "slippage_bps": 2.0,
    }
    better = {
        "daily_returns": [0.0015] * 90,
        "max_drawdown": 0.12,
        "reject_rate": 0.01,
        "slippage_bps": 2.0,
    }

    s_base = evaluate_candidate(base)["score_total"]
    s_better = evaluate_candidate(better)["score_total"]

    assert s_better > s_base
