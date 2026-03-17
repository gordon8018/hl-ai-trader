# tests/test_ai_decision_layer1.py
import os
import sys
import importlib
import json
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


def test_parse_direction_bias_valid():
    mod = load_ai()
    raw = json.dumps({
        "biases": [
            {"symbol": "BTC", "direction": "LONG", "confidence": 0.72},
            {"symbol": "ETH", "direction": "FLAT", "confidence": 0.45},
        ],
        "market_state": "TRENDING",
        "rationale": "strong trend"
    })
    db = mod.parse_direction_bias(raw, ["BTC", "ETH", "SOL", "ADA", "DOGE"])
    assert db is not None
    assert db.market_state == "TRENDING"
    assert len(db.biases) == 5  # All 5 symbols filled (missing 3 → FLAT)
    btc = next(b for b in db.biases if b.symbol == "BTC")
    assert btc.direction == "LONG"


def test_parse_direction_bias_missing_symbols_filled_flat():
    mod = load_ai()
    # LLM only returned BTC — missing symbols should be filled as FLAT
    raw = json.dumps({
        "biases": [{"symbol": "BTC", "direction": "LONG", "confidence": 0.70}],
        "market_state": "TRENDING",
    })
    db = mod.parse_direction_bias(raw, ["BTC", "ETH"])
    symbols = {b.symbol for b in db.biases}
    assert "ETH" in symbols
    eth = next(b for b in db.biases if b.symbol == "ETH")
    assert eth.direction == "FLAT"


def test_parse_direction_bias_invalid_json_returns_none():
    mod = load_ai()
    db = mod.parse_direction_bias("not json", ["BTC", "ETH"])
    assert db is None


def test_is_direction_bias_valid_true():
    mod = load_ai()
    from shared.schemas import DirectionBias, SymbolBias
    db = DirectionBias(
        asof_minute="2026-03-16T15:00:00Z",
        valid_until_minute="2026-03-16T16:00:00Z",
        market_state="TRENDING",
        biases=[SymbolBias(symbol="BTC", direction="LONG", confidence=0.70)],
    )
    assert mod.is_direction_bias_valid(db, "2026-03-16T15:00:00Z") is True


def test_is_direction_bias_valid_expired():
    mod = load_ai()
    from shared.schemas import DirectionBias, SymbolBias
    db = DirectionBias(
        asof_minute="2026-03-16T13:00:00Z",
        valid_until_minute="2026-03-16T14:00:00Z",   # expired
        market_state="TRENDING",
        biases=[SymbolBias(symbol="BTC", direction="LONG", confidence=0.70)],
    )
    assert mod.is_direction_bias_valid(db, "2026-03-16T15:00:00Z") is False
