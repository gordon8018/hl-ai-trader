from research.promotion_orchestrator import evaluate_and_decide


def test_evaluate_and_decide_rolls_back_on_threshold_breach():
    out = evaluate_and_decide(
        metrics={"drawdown": 0.25, "reject_rate": 0.10, "slippage_bps": 12.0},
        thresholds={
            "max_drawdown": 0.20,
            "max_reject_rate": 0.05,
            "max_slippage_bps": 8.0,
        },
    )
    assert out["action"] == "rollback"


def test_evaluate_and_decide_rejects_malformed_input_without_raising():
    out = evaluate_and_decide(
        metrics={"drawdown": "bad", "reject_rate": 0.10, "slippage_bps": 12.0},
        thresholds={
            "max_drawdown": 0.20,
            "max_reject_rate": 0.05,
            "max_slippage_bps": 8.0,
        },
    )
    assert out["action"] == "rollback"
    assert out["reason"] == "invalid_input"
