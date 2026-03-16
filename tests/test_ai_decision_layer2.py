# tests/test_ai_decision_layer2.py
import os
import sys
import importlib
import pytest
from shared.schemas import DirectionBias, SymbolBias, FeatureSnapshot15m


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    yield


def load_ai():
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def make_bias(symbol_dirs: dict, market_state="TRENDING") -> DirectionBias:
    return DirectionBias(
        asof_minute="2026-03-16T15:00:00Z",
        valid_until_minute="2026-03-16T16:00:00Z",
        market_state=market_state,
        biases=[
            SymbolBias(symbol=sym, direction=d, confidence=0.72)
            for sym, d in symbol_dirs.items()
        ],
    )


def make_fs15(sym_overrides: dict = None) -> FeatureSnapshot15m:
    """Create a FeatureSnapshot15m with all signals favoring LONG for BTC."""
    base = {
        "asof_minute": "2026-03-16T15:00:00Z",
        "window_start_minute": "2026-03-16T14:45:00Z",
        "universe": ["BTC", "ETH", "SOL", "ADA", "DOGE"],
        "mid_px": {"BTC": 70000.0, "ETH": 2100.0, "SOL": 90.0, "ADA": 0.28, "DOGE": 0.095},
        "funding_rate": {"BTC": 0.0001, "ETH": 0.0001, "SOL": 0.0001, "ADA": 0.0001, "DOGE": 0.0001},
        "basis_bps": {"BTC": 5.0, "ETH": 4.0, "SOL": 3.0, "ADA": 2.0, "DOGE": 2.0},
        "oi_change_15m": {"BTC": 100.0, "ETH": 50.0, "SOL": 30.0, "ADA": 20.0, "DOGE": 10.0},
        "book_imbalance_l5": {"BTC": 0.15, "ETH": 0.12, "SOL": 0.11, "ADA": 0.10, "DOGE": 0.10},
        "aggr_delta_5m": {"BTC": 500.0, "ETH": 200.0, "SOL": 100.0, "ADA": 50.0, "DOGE": 30.0},
        "trend_strength_15m": {"BTC": 0.7, "ETH": 0.65, "SOL": 0.62, "ADA": 0.61, "DOGE": 0.60},
        "ret_15m": {"BTC": 0.005, "ETH": 0.004, "SOL": 0.003, "ADA": 0.003, "DOGE": 0.003},
        "vol_15m": {"BTC": 0.01, "ETH": 0.012, "SOL": 0.015, "ADA": 0.018, "DOGE": 0.020},
    }
    if sym_overrides:
        for k, v in sym_overrides.items():
            base[k] = v
    return FeatureSnapshot15m(**base)


def test_flat_direction_returns_zero_weight():
    mod = load_ai()
    bias = make_bias({"BTC": "FLAT"})
    fs = make_fs15()
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] == 0.0


def test_long_with_3_signals_returns_positive_weight():
    mod = load_ai()
    bias = make_bias({"BTC": "LONG"})
    fs = make_fs15()
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] > 0.0


def test_long_with_insufficient_signals_returns_zero():
    mod = load_ai()
    bias = make_bias({"BTC": "LONG"})
    fs = make_fs15({
        "funding_rate": {"BTC": -0.001},
        "basis_bps": {"BTC": -2.0},
        "oi_change_15m": {"BTC": -50.0},
        "book_imbalance_l5": {"BTC": -0.05},
        "aggr_delta_5m": {"BTC": -100.0},
        "trend_strength_15m": {"BTC": 0.3},
    })
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] == 0.0


def test_short_with_3_signals_returns_negative_weight():
    mod = load_ai()
    bias = make_bias({"BTC": "SHORT"})
    fs = make_fs15({
        "funding_rate": {"BTC": -0.0001},
        "basis_bps": {"BTC": -5.0},
        "oi_change_15m": {"BTC": -100.0},
        "book_imbalance_l5": {"BTC": -0.15},
        "aggr_delta_5m": {"BTC": -500.0},
        "trend_strength_15m": {"BTC": 0.7},  # strong trend exists (direction-agnostic)
    })
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] < 0.0


def test_sideways_market_all_flat():
    mod = load_ai()
    bias = make_bias({"BTC": "LONG", "ETH": "LONG"}, market_state="SIDEWAYS")
    fs = make_fs15()
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC", "ETH"])
    assert weights["BTC"] == 0.0
    assert weights["ETH"] == 0.0
