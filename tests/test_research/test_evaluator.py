"""Tests for research.factors.evaluator."""
from __future__ import annotations
import numpy as np
import polars as pl
import pytest
from research.factors.evaluator import (
    compute_ic_series,
    compute_factor_metrics,
    compute_quantile_returns,
    compute_factor_autocorrelation,
    evaluate_all_factors,
)


def _make_df(n: int = 200, seed: int = 42, symbols: list[str] | None = None) -> pl.DataFrame:
    """Build a synthetic DataFrame with factor and forward-return columns."""
    rng = np.random.default_rng(seed)
    if symbols is None:
        symbols = ["BTC", "ETH"]
    rows_per_sym = n // len(symbols)
    frames = []
    for sym in symbols:
        factor_a = rng.standard_normal(rows_per_sym)
        factor_b = rng.standard_normal(rows_per_sym)
        # ret_fwd_1h is positively correlated with factor_a
        ret_1h = factor_a * 0.5 + rng.standard_normal(rows_per_sym) * 0.5
        # ret_fwd_4h is positively correlated with factor_b
        ret_4h = factor_b * 0.5 + rng.standard_normal(rows_per_sym) * 0.5
        ts = list(range(rows_per_sym))
        frames.append(
            pl.DataFrame(
                {
                    "ts": ts,
                    "symbol": [sym] * rows_per_sym,
                    "factor_a": factor_a.tolist(),
                    "factor_b": factor_b.tolist(),
                    "ret_fwd_1h": ret_1h.tolist(),
                    "ret_fwd_4h": ret_4h.tolist(),
                }
            )
        )
    return pl.concat(frames)


def test_compute_ic_series():
    df = _make_df(n=300)
    # factor_a is positively correlated with ret_fwd_1h
    ic = compute_ic_series(df, "factor_a", "ret_fwd_1h", window=48)
    assert isinstance(ic, np.ndarray)
    assert len(ic) > 0
    assert float(np.mean(ic)) > 0.0, f"Expected positive IC mean, got {np.mean(ic)}"


def test_compute_factor_metrics():
    df = _make_df(n=300)
    metrics = compute_factor_metrics(df, "factor_a", "ret_fwd_1h")
    assert "ic_mean" in metrics
    assert "ic_std" in metrics
    assert "icir" in metrics
    assert "ic_count" in metrics
    assert metrics["ic_count"] > 0
    # With a signal, IC should be meaningfully positive
    assert metrics["ic_mean"] > 0.0


def test_compute_quantile_returns():
    df = _make_df(n=400)
    result = compute_quantile_returns(df, "factor_a", "ret_fwd_1h", n_quantiles=5)
    # Should return a DataFrame with 5 quantiles
    assert len(result) == 5
    assert "quantile" in result.columns
    assert "mean_return" in result.columns
    assert "count" in result.columns
    assert "monotonicity" in result.columns

    sorted_result = result.sort("quantile")
    highest = sorted_result["mean_return"][-1]
    lowest = sorted_result["mean_return"][0]
    # Positive correlation: highest quantile should have higher mean return
    assert highest > lowest, f"Expected highest > lowest, got {highest} vs {lowest}"

    # Monotonicity should be non-trivial (factor_a has clear signal)
    mono = result["monotonicity"][0]
    assert not np.isnan(mono)


def test_compute_factor_autocorrelation():
    rng = np.random.default_rng(0)
    n = 300
    # Cumulative sum gives a highly autocorrelated series
    vals = np.cumsum(rng.standard_normal(n))
    df = pl.DataFrame(
        {
            "ts": list(range(n)),
            "symbol": ["BTC"] * n,
            "cumsum_factor": vals.tolist(),
        }
    )
    autocorr = compute_factor_autocorrelation(df, "cumsum_factor", lag=1)
    assert autocorr > 0.9, f"Expected high autocorr for cumsum, got {autocorr}"


def test_evaluate_all_factors():
    df = _make_df(n=400)
    result = evaluate_all_factors(
        df,
        factor_cols=["factor_a", "factor_b"],
        return_cols=["ret_fwd_1h", "ret_fwd_4h"],
    )
    # 2 factors × 2 horizons = 4 rows
    assert len(result) == 4, f"Expected 4 rows, got {len(result)}"
    assert "factor" in result.columns
    assert "horizon" in result.columns
    assert "ic_mean" in result.columns
    assert "icir" in result.columns
    assert "autocorrelation" in result.columns
    # Results should be sorted by |icir| descending
    abs_icirs = result["icir"].abs().to_list()
    assert abs_icirs == sorted(abs_icirs, reverse=True)
