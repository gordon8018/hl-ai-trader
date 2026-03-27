from pathlib import Path

from research.autoresearch_runner import run_one_iteration
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


def test_malformed_daily_returns_are_fail_soft():
    out_str = evaluate_candidate(
        {
            "daily_returns": "not-an-iterable-of-returns",
            "max_drawdown": 0.20,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        }
    )
    out_dict = evaluate_candidate(
        {
            "daily_returns": {"bad": "shape"},
            "max_drawdown": 0.20,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        }
    )
    out_mixed = evaluate_candidate(
        {
            "daily_returns": [0.001, "bad", None, 0.002],
            "max_drawdown": 0.20,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        }
    )

    for out in (out_str, out_dict, out_mixed):
        assert set(out) == {
            "score_total",
            "score_sharpe",
            "score_calmar",
            "penalty_dd",
            "penalty_exec",
        }
        assert all(isinstance(v, float) for v in out.values())

    assert out_str["score_total"] == out_dict["score_total"]
    assert out_mixed["score_total"] > out_str["score_total"]


def test_negative_drawdown_uses_magnitude():
    positive = evaluate_candidate(
        {
            "daily_returns": [0.001] * 90,
            "max_drawdown": 0.20,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        }
    )
    negative = evaluate_candidate(
        {
            "daily_returns": [0.001] * 90,
            "max_drawdown": -0.20,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        }
    )

    assert negative["score_total"] == positive["score_total"]
    assert negative["penalty_dd"] == positive["penalty_dd"]


def test_run_one_iteration_returns_candidate(tmp_path: Path):
    out = run_one_iteration(
        output_root=tmp_path,
        baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
        observed_metrics={
            "daily_returns": [0.001] * 90,
            "max_drawdown": 0.18,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        },
    )

    assert out["profile_name"].startswith("V9_ar_")
    assert out["score"]["score_total"] is not None
    pack_dir = Path(out["pack_dir"])
    assert pack_dir.exists()
    assert (pack_dir / "candidate_profile.json").exists()
    assert (pack_dir / "report.md").exists()
    assert (pack_dir / "diff.md").exists()
    assert (pack_dir / "risk_notes.md").exists()


def test_run_one_iteration_uses_unique_profile_names(tmp_path: Path):
    out1 = run_one_iteration(
        output_root=tmp_path,
        baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
        observed_metrics={
            "daily_returns": [0.001] * 90,
            "max_drawdown": 0.18,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        },
    )
    out2 = run_one_iteration(
        output_root=tmp_path,
        baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
        observed_metrics={
            "daily_returns": [0.001] * 90,
            "max_drawdown": 0.18,
            "reject_rate": 0.01,
            "slippage_bps": 2.0,
        },
    )

    assert out1["profile_name"] != out2["profile_name"]
