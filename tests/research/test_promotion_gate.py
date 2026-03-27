from research.promotion_gate import evaluate_promotion_gate


def test_gate_rejects_when_drawdown_worsens():
    decision = evaluate_promotion_gate(
        candidate={
            "score_total": 1.8,
            "max_drawdown": 0.24,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        },
        baseline={
            "score_total": 1.5,
            "max_drawdown": 0.18,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        },
        thresholds={
            "max_dd_delta": 0.00,
            "max_reject_rate": 0.05,
            "max_slippage_bps": 8.0,
        },
    )

    assert decision["pass"] is False
    assert "drawdown_regression" in decision["reasons"]


def test_gate_rejects_malformed_input_without_raising():
    decision = evaluate_promotion_gate(
        candidate="not-a-dict",
        baseline={
            "score_total": 1.5,
            "max_drawdown": 0.18,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        },
        thresholds={
            "max_dd_delta": 0.00,
            "max_reject_rate": 0.05,
            "max_slippage_bps": 8.0,
        },
    )

    assert decision["pass"] is False
    assert "invalid_candidate" in decision["reasons"]
