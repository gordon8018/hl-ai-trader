"""Tests for research.factors.correlation."""
from __future__ import annotations
import numpy as np
import polars as pl
import pytest
from research.factors.correlation import compute_correlation_matrix, find_redundant_factors


def _make_corr_df(n: int = 200, seed: int = 7) -> pl.DataFrame:
    """Build a DataFrame with two correlated and one uncorrelated factor."""
    rng = np.random.default_rng(seed)
    base = rng.standard_normal(n)
    # factor_x and factor_y are highly correlated (both track base)
    factor_x = base + rng.standard_normal(n) * 0.05
    factor_y = base + rng.standard_normal(n) * 0.05
    # factor_z is independent
    factor_z = rng.standard_normal(n)
    return pl.DataFrame(
        {
            "factor_x": factor_x.tolist(),
            "factor_y": factor_y.tolist(),
            "factor_z": factor_z.tolist(),
        }
    )


def test_compute_correlation_matrix():
    df = _make_corr_df(n=300)
    cols = ["factor_x", "factor_y", "factor_z"]
    corr = compute_correlation_matrix(df, cols)

    assert corr.shape == (3, 3)
    # Diagonal must be 1
    np.testing.assert_allclose(np.diag(corr), 1.0, atol=1e-10)

    # Correlated pair (x, y) should have correlation > 0.9
    assert corr[0, 1] > 0.9, f"Expected corr(x,y) > 0.9, got {corr[0, 1]}"
    assert corr[1, 0] > 0.9

    # Uncorrelated pair (x, z) and (y, z) should have |correlation| < 0.3
    assert abs(corr[0, 2]) < 0.3, f"Expected |corr(x,z)| < 0.3, got {corr[0, 2]}"
    assert abs(corr[1, 2]) < 0.3, f"Expected |corr(y,z)| < 0.3, got {corr[1, 2]}"


def test_find_redundant_factors():
    df = _make_corr_df(n=300)
    cols = ["factor_x", "factor_y", "factor_z"]

    # factor_x has higher |ICIR| than factor_y → factor_y should be flagged
    icir_map = {"factor_x": 0.8, "factor_y": 0.3, "factor_z": 0.5}
    redundant = find_redundant_factors(df, cols, icir_map, threshold=0.8)

    assert isinstance(redundant, set)
    # factor_y should be flagged as redundant (lower ICIR than factor_x, highly correlated)
    assert "factor_y" in redundant, f"Expected factor_y in redundant, got {redundant}"
    # factor_x should NOT be flagged (higher ICIR)
    assert "factor_x" not in redundant
    # factor_z is uncorrelated, should not be flagged
    assert "factor_z" not in redundant
