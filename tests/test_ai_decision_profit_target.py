# tests/test_ai_decision_profit_target.py
"""测试持仓主动止盈逻辑：当open持仓浮动PnL超过POSITION_PROFIT_TARGET_BPS时强制归零。"""
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
                "MAX_GROSS": 0.35, "MAX_NET": 0.20,
                "CAP_BTC_ETH": 0.30, "CAP_ALT": 0.15,
                "AI_SMOOTH_ALPHA": 0.25, "AI_SMOOTH_ALPHA_HIGH": 0.60,
                "AI_SMOOTH_ALPHA_MID": 0.40, "AI_MIN_CONFIDENCE": 0.50,
                "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75, "MAX_GROSS_HIGH": 0.50,
                "MAX_NET_HIGH": 0.35, "AI_TURNOVER_CAP": 0.05,
                "AI_TURNOVER_CAP_HIGH": 0.20, "AI_SIGNAL_DELTA_THRESHOLD": 0.15,
                "AI_MIN_MAJOR_INTERVAL_MIN": 30, "DIRECTION_REVERSAL_WINDOW_MIN": 30,
                "DIRECTION_REVERSAL_THRESHOLD": 2, "DIRECTION_REVERSAL_PENALTY": "zero",
                "COOLDOWN_MINUTES": 15, "RECENT_PNL_WINDOW": 5,
                "MAX_CONSECUTIVE_LOSS": 2, "PNL_DISABLE_DURATION_MIN": 60,
                "RECENT_LOSS_SCALE_FACTOR": 0.3, "RECENT_LOSS_THRESHOLD": 3.0,
                "DAILY_DRAWDOWN_HALT_USD": 3.0, "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
                "PORTFOLIO_LOSS_COUNTER_SHARED": True, "SCALE_BY_RECENT_LOSS": True,
                "FORCE_NET_DIRECTION": True, "MAX_NET_LONG_WHEN_DOWN": 0.0,
                "MAX_NET_SHORT_WHEN_UP": 0.0, "BEARISH_REGIME_LONG_BLOCK": True,
                "BEARISH_REGIME_RET1H_THRESHOLD": -0.005, "MAX_SLIPPAGE_EMERGENCY": 5,
                "PRICE_DROP_EMERGENCY_PCT": 2.0, "FORCE_CASH_WHEN_EXTREME": True,
                "VOL_REGIME_DEFENSIVE": 1, "TREND_AGREE_DEFENSIVE": True,
                "EXEC_DEFENSIVE_REJECT": 0.05, "EXEC_DEFENSIVE_LATENCY": 500,
                "EXEC_DEFENSIVE_SLIPPAGE": 8, "ORDER_CONSOLIDATE_PER_CYCLE": True,
                "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
                "POSITION_MAX_AGE_MIN": 30,
                "POSITION_PROFIT_TARGET_BPS": 25.0,
                "AI_DECISION_HORIZON": "30m", "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "", "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "", "AI_LLM_MODEL": "", "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.1m", "STREAM_IN_1H": "md.features.1h",
                "CONSUMER": "ai_1", "CONSUMER_1H": "ai_layer1_1",
                "MAX_RETRIES": 5, "ERROR_STREAK_THRESHOLD": 3,
                "MIN_NOTIONAL_USD": 50.0, "MAX_TRADES_PER_DAY": 30,
                "MAX_GROSS_TRENDING_HIGH": 0.65, "MAX_GROSS_TRENDING_MID": 0.45,
                "MAX_GROSS_SIDEWAYS": 0.00, "MAX_GROSS_VOLATILE": 0.20,
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
            }
        }
    }
    config_file = tmp_path / "test_trading_params.json"
    config_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AI_CONFIG_PATH", str(config_file))
    yield


def load_ai():
    import sys, importlib
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_apply_profit_target_closes_profitable_position():
    """持仓盈利超过POSITION_PROFIT_TARGET_BPS时，apply_profit_target应将权重归零。"""
    mod = load_ai()
    current_weights = {"BTC": 0.20, "ETH": 0.0}
    position_pnl_bps = {"BTC": 30.0, "ETH": 0.0}  # BTC盈利30bps > 25bps目标
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.0, "BTC盈利超目标，应被平仓（归零）"
    assert result["ETH"] == 0.0, "ETH无持仓，保持零"


def test_apply_profit_target_keeps_underperforming_position():
    """持仓盈利未达目标时，apply_profit_target不应改变权重。"""
    mod = load_ai()
    current_weights = {"BTC": 0.20}
    position_pnl_bps = {"BTC": 10.0}  # 10bps < 25bps目标
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.20, "未达止盈目标，权重不变"


def test_apply_profit_target_ignores_zero_positions():
    """空仓位不受影响。"""
    mod = load_ai()
    current_weights = {"BTC": 0.0}
    position_pnl_bps = {"BTC": 100.0}
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.0


def test_apply_profit_target_handles_short_position():
    """SHORT持仓（负权重）盈利超目标时也应归零。"""
    mod = load_ai()
    current_weights = {"BTC": -0.15}
    position_pnl_bps = {"BTC": 30.0}  # SHORT盈利30bps > 25bps
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.0, "SHORT持仓盈利超目标，应归零"
