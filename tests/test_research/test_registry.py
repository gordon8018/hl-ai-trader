"""Tests for research.factors.registry."""
from __future__ import annotations
import pytest
from research.factors.registry import (
    FACTOR_FAMILIES,
    get_all_factors,
    get_factors_for_timeframe,
    get_family,
)


def test_registry_lists_all_factors():
    factors = get_all_factors()
    # Must have more than 50 factors
    assert len(factors) > 50, f"Expected >50 factors, got {len(factors)}"
    # No duplicates
    assert len(factors) == len(set(factors)), "Duplicate factor names found in registry"


def test_get_factors_for_timeframe():
    # 1m factors must be a non-empty subset
    factors_1m = get_factors_for_timeframe("1m")
    assert len(factors_1m) > 0, "No factors found for 1m timeframe"

    factors_15m = get_factors_for_timeframe("15m")
    assert len(factors_15m) > 0, "No factors found for 15m timeframe"

    factors_1h = get_factors_for_timeframe("1h")
    assert len(factors_1h) > 0, "No factors found for 1h timeframe"

    # 15m should have more factors than 1h (more features available at 15m)
    assert len(factors_15m) >= len(factors_1h)

    # All returned factors must exist in the full registry
    all_factors = set(get_all_factors())
    for f in factors_1m:
        assert f in all_factors
    for f in factors_15m:
        assert f in all_factors

    # Spot-check: known 1m-only factor should not appear in 1h
    assert "book_imbalance" in factors_1m
    assert "book_imbalance" not in factors_1h


def test_get_family():
    assert get_family("book_imbalance_l1") == "microstructure"
    assert get_family("ret_1h") == "momentum"
    assert get_family("funding_rate") == "funding_basis"
    assert get_family("nonexistent_factor_xyz") is None
