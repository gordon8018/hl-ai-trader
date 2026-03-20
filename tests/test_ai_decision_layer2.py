# tests/test_ai_decision_layer2.py
import os
import sys
import importlib
import pytest
from shared.schemas import DirectionBias, SymbolBias, FeatureSnapshot15m


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
                "POSITION_PROFIT_TARGET_BPS": 25.0,
                "AI_DECISION_HORIZON": "30m",
                "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "",
                "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "",
                "AI_LLM_MODEL": "",
                "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.15m",
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


def test_direction_confirmation_logs_regime_info(caplog):
    import logging
    mod = load_ai()
    bias = make_bias({"BTC": "LONG"})
    fs = make_fs15()
    with caplog.at_level(logging.INFO, logger="services.ai_decision.app"):
        mod.apply_direction_confirmation(fs, bias, ["BTC"])
    log_text = " ".join(caplog.messages)
    # 应记录 market_state 和 active/blocked 信息
    assert "market_state" in log_text or "Layer2" in log_text


def test_stream_in_default_is_15m_features():
    """Layer 2 应订阅 md.features.15m，而非 md.features.1m。
    trend_agree/trend_15m/trend_1h 等趋势确认字段仅在 15m 流中发布。
    """
    mod = load_ai()
    assert mod.STREAM_IN == "md.features.15m", (
        f"STREAM_IN 默认值错误: got '{mod.STREAM_IN}', "
        "Layer 2 必须订阅 md.features.15m 才能获取 trend_agree 等趋势确认字段"
    )


def test_direction_confirmation_logs_short_confirm(caplog):
    import logging
    mod = load_ai()
    bias = make_bias({"BTC": "SHORT"})
    fs = make_fs15({
        "funding_rate": {"BTC": -0.0001},
        "basis_bps": {"BTC": -5.0},
        "oi_change_15m": {"BTC": -100.0},
        "book_imbalance_l5": {"BTC": -0.15},
        "aggr_delta_5m": {"BTC": -500.0},
        "trend_strength_15m": {"BTC": 0.7},
    })
    with caplog.at_level(logging.DEBUG, logger="services.ai_decision.app"):
        result = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert result["BTC"] < 0.0
    # debug日志中应包含 BTC 和 confirm 计数
    log_text = " ".join(caplog.messages)
    assert "BTC" in log_text
