# tests/test_ai_decision_capital.py
import os
import sys
import importlib
import pytest


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


def test_select_max_gross_trending_high():
    mod = load_ai()
    # >= 0.70 (MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD) -> HIGH
    gross = mod.select_max_gross("TRENDING", confidence=0.72)
    assert gross == mod.MAX_GROSS_TRENDING_HIGH


def test_select_max_gross_trending_mid():
    mod = load_ai()
    # < 0.70 -> MID
    gross = mod.select_max_gross("TRENDING", confidence=0.65)
    assert gross == mod.MAX_GROSS_TRENDING_MID


def test_select_max_gross_sideways():
    mod = load_ai()
    gross = mod.select_max_gross("SIDEWAYS", confidence=0.80)
    assert gross == 0.0


def test_select_max_gross_volatile():
    mod = load_ai()
    gross = mod.select_max_gross("VOLATILE", confidence=0.72)
    assert gross == mod.MAX_GROSS_VOLATILE


def test_select_smooth_alpha_high_confidence():
    mod = load_ai()
    alpha = mod.select_smooth_alpha(confidence=0.72)
    assert alpha == mod.AI_SMOOTH_ALPHA_HIGH   # 0.60


def test_select_smooth_alpha_mid_confidence():
    mod = load_ai()
    alpha = mod.select_smooth_alpha(confidence=0.60)
    assert alpha == mod.AI_SMOOTH_ALPHA_MID    # 0.40


def test_select_smooth_alpha_low_confidence():
    mod = load_ai()
    alpha = mod.select_smooth_alpha(confidence=0.40)
    assert alpha == mod.AI_SMOOTH_ALPHA        # 0.25


def test_apply_min_notional_filters_small_positions():
    mod = load_ai()
    weights = {"BTC": 0.001, "ETH": 0.10, "SOL": 0.0}
    equity = 1000.0
    result = mod.apply_min_notional(weights, equity, min_notional_usd=50.0)
    assert result["BTC"] == 0.0   # 0.001 * 1000 = $1 < $50 -> filtered
    assert result["ETH"] == 0.10  # 0.10 * 1000 = $100 > $50 -> kept
    assert result["SOL"] == 0.0   # already 0
