# tests/test_ai_decision_capital.py
import os
import sys
import importlib
import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    cfg = {
        "active_version": "TEST",
        "versions": {
            "TEST": {
                "UNIVERSE": "BTC,ETH,SOL,ADA,DOGE",
                "REDIS_URL": "redis://localhost:6379/0",
                "MAX_GROSS": 0.35,
                "MAX_NET": 0.20,
                "CAP_BTC_ETH": 0.30,
                "CAP_ALT": 0.15,
                "AI_SMOOTH_ALPHA": 0.25,
                "AI_SMOOTH_ALPHA_HIGH": 0.60,
                "AI_SMOOTH_ALPHA_MID": 0.40,
                "AI_MIN_CONFIDENCE": 0.50,
                "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75,
                "MAX_GROSS_HIGH": 0.50,
                "MAX_NET_HIGH": 0.35,
                "AI_TURNOVER_CAP": 0.05,
                "AI_TURNOVER_CAP_HIGH": 0.20,
                "AI_SIGNAL_DELTA_THRESHOLD": 0.15,
                "AI_MIN_MAJOR_INTERVAL_MIN": 30,
                "DIRECTION_REVERSAL_WINDOW_MIN": 30,
                "DIRECTION_REVERSAL_THRESHOLD": 2,
                "DIRECTION_REVERSAL_PENALTY": "zero",
                "COOLDOWN_MINUTES": 15,
                "RECENT_PNL_WINDOW": 5,
                "MAX_CONSECUTIVE_LOSS": 2,
                "PNL_DISABLE_DURATION_MIN": 60,
                "RECENT_LOSS_SCALE_FACTOR": 0.3,
                "RECENT_LOSS_THRESHOLD": 3.0,
                "DAILY_DRAWDOWN_HALT_USD": 3.0,
                "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
                "PORTFOLIO_LOSS_COUNTER_SHARED": True,
                "SCALE_BY_RECENT_LOSS": True,
                "FORCE_NET_DIRECTION": True,
                "MAX_NET_LONG_WHEN_DOWN": 0.0,
                "MAX_NET_SHORT_WHEN_UP": 0.0,
                "BEARISH_REGIME_LONG_BLOCK": True,
                "BEARISH_REGIME_RET1H_THRESHOLD": -0.005,
                "MAX_SLIPPAGE_EMERGENCY": 5,
                "PRICE_DROP_EMERGENCY_PCT": 2.0,
                "FORCE_CASH_WHEN_EXTREME": True,
                "VOL_REGIME_DEFENSIVE": 1,
                "TREND_AGREE_DEFENSIVE": True,
                "EXEC_DEFENSIVE_REJECT": 0.05,
                "EXEC_DEFENSIVE_LATENCY": 500,
                "EXEC_DEFENSIVE_SLIPPAGE": 8,
                "ORDER_CONSOLIDATE_PER_CYCLE": True,
                "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
                "POSITION_MAX_AGE_MIN": 30,
                "POSITION_PROFIT_TARGET_BPS": 15.0,
                "AI_DECISION_HORIZON": "30m",
                "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "",
                "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "",
                "AI_LLM_MODEL": "",
                "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.1m",
                "STREAM_IN_1H": "md.features.1h",
                "CONSUMER": "ai_1",
                "CONSUMER_1H": "ai_layer1_1",
                "MAX_RETRIES": 5,
                "ERROR_STREAK_THRESHOLD": 3,
                "MIN_NOTIONAL_USD": 50.0,
                "MAX_TRADES_PER_DAY": 30,
                "MAX_GROSS_TRENDING_HIGH": 0.65,
                "MAX_GROSS_TRENDING_MID": 0.45,
                "MAX_GROSS_SIDEWAYS": 0.00,
                "MAX_GROSS_VOLATILE": 0.20,
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
            }
        }
    }
    import json
    config_file = tmp_path / "test_trading_params.json"
    config_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AI_CONFIG_PATH", str(config_file))
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
