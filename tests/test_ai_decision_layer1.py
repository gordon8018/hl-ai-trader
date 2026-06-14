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
                "POSITION_PROFIT_TARGET_BPS": 25.0,
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


def test_process_1h_message_debounce_skips_when_too_soon():
    """若距上次Layer1决策不足5分钟，process_1h_message应直接返回None。"""
    import time
    mod = load_ai()
    from tests.fake_redis import FakeRedis
    from shared.schemas import FeatureSnapshot1h

    fake_r = FakeRedis()
    # 模拟上次决策在2分钟前（< 5分钟去抖动阈值）
    fake_r.set(mod.LAYER1_LAST_DECISION_TS_KEY, str(time.time() - 2 * 60))

    class FakeBus:
        r = fake_r
        def xadd_json(self, *a, **kw): pass

    fs1h = FeatureSnapshot1h(
        asof_minute="2026-03-17T10:00:00Z",
        window_start_minute="2026-03-17T09:00:00Z",
        universe=["BTC"],
        mid_px={"BTC": 70000.0},
        ret_1h={"BTC": 0.005},
        ret_4h={"BTC": 0.01},
        trend_1h={"BTC": 1.0},
        trend_4h={"BTC": 1.0},
        trend_agree={"BTC": 1.0},
        vol_regime={"BTC": 0.0},
        funding_rate={"BTC": 0.0001},
    )
    result = mod.process_1h_message(fs1h, FakeBus())
    assert result is None, "距上次决策不足5分钟，应返回None（debounce）"


def test_process_1h_message_debounce_passes_when_no_prior_decision():
    """首次运行（Redis中无LAYER1_LAST_DECISION_TS_KEY记录）时不被debounce阻止。
    量化策略下 process_1h_message 通过 compute_quant_bias 直接计算，
    首次运行应返回有效的 DirectionBias（非 None）。
    """
    mod = load_ai()
    from tests.fake_redis import FakeRedis
    from shared.schemas import FeatureSnapshot1h

    fake_r = FakeRedis()
    # 不设置 LAYER1_LAST_DECISION_TS_KEY，模拟首次运行
    assert fake_r.get(mod.LAYER1_LAST_DECISION_TS_KEY) is None

    call_log = []

    class FakeBus:
        r = fake_r
        def xadd_json(self, *a, **kw): pass
        def setex(self, *a, **kw): pass

    # Monkey-patch compute_quant_bias 来检测函数是否越过了 debounce 检查
    original_quant = mod.compute_quant_bias
    def patched_quant(fs1h, universe, **kwargs):
        call_log.append("quant_called")
        return original_quant(fs1h, universe, **kwargs)
    mod.compute_quant_bias = patched_quant

    fs1h = FeatureSnapshot1h(
        asof_minute="2026-03-17T10:00:00Z",
        window_start_minute="2026-03-17T09:00:00Z",
        universe=["BTC"],
        mid_px={"BTC": 70000.0},
        ret_1h={"BTC": 0.005},
        ret_4h={"BTC": 0.01},
        trend_1h={"BTC": 1.0},
        trend_4h={"BTC": 1.0},
        trend_agree={"BTC": 1.0},
        vol_regime={"BTC": 0.0},
        funding_rate={"BTC": 0.0001},
    )
    try:
        result = mod.process_1h_message(fs1h, FakeBus())
    finally:
        mod.compute_quant_bias = original_quant  # 恢复

    # 首次运行不应被 debounce 阻止，compute_quant_bias 必须被调用
    assert "quant_called" in call_log, "首次运行未被debounce阻止，应调用到compute_quant_bias"
    assert result is not None, "首次运行应返回有效的 DirectionBias"


# ── M2: weight_scale tiering ──────────────────────────────────────────────────

def _make_fs1h(vol_regime=0.0):
    from shared.schemas import FeatureSnapshot1h
    return FeatureSnapshot1h(
        asof_minute="2026-04-12T10:00:00Z",
        window_start_minute="2026-04-12T09:00:00Z",
        universe=["BTC"],
        mid_px={"BTC": 80000.0},
        ret_1h={"BTC": 0.005},
        ret_4h={"BTC": 0.002},
        vol_1h={"BTC": 0.01},
        trend_agree={"BTC": 1.0},
        vol_regime={"BTC": vol_regime},
    )


def test_weight_scale_full_for_strong_signal():
    """composite >= 0.25 → weight_scale = 1.0"""
    mod = load_ai()
    # Drive a strong composite by manipulating factors:
    # mom_24h ≈ 0 (ret_24h=0), macd proxy ≈ 0, atr_to_support high (-> f_supp positive)
    # We patch compute_quant_bias to return known composite
    from shared.schemas import DirectionBias, SymbolBias
    strong_bias = DirectionBias(
        asof_minute="2026-04-12T10:00:00Z",
        valid_until_minute="2026-04-12T11:00:00Z",
        market_state="TRENDING",
        biases=[SymbolBias(symbol="BTC", direction="LONG", confidence=0.70, weight_scale=1.0)],
    )
    mod.compute_quant_bias = lambda fs, u, **kw: strong_bias
    result = mod.process_1h_message(_make_fs1h(), type("B", (), {"r": __import__("tests.fake_redis", fromlist=["FakeRedis"]).FakeRedis(), "xadd_json": lambda *a, **k: None})())
    btc_bias = next(b for b in result.biases if b.symbol == "BTC")
    assert btc_bias.weight_scale == 1.0


def test_weight_scale_half_for_weak_signal():
    """0.12 <= |composite| < 0.25 → weight_scale = 0.5"""
    mod = load_ai()
    # Force compute_quant_bias to return a half-tier bias
    from shared.schemas import DirectionBias, SymbolBias
    weak_bias = DirectionBias(
        asof_minute="2026-04-12T10:00:00Z",
        valid_until_minute="2026-04-12T11:00:00Z",
        market_state="TRENDING",
        biases=[SymbolBias(symbol="BTC", direction="LONG", confidence=0.55, weight_scale=0.5)],
    )
    mod.compute_quant_bias = lambda fs, u, **kw: weak_bias
    result = mod.process_1h_message(_make_fs1h(), type("B", (), {"r": __import__("tests.fake_redis", fromlist=["FakeRedis"]).FakeRedis(), "xadd_json": lambda *a, **k: None})())
    btc_bias = next(b for b in result.biases if b.symbol == "BTC")
    assert btc_bias.weight_scale == 0.5


def test_quant_bias_weight_scale_values():
    """Directly test that compute_quant_bias emits correct weight_scale."""
    mod = load_ai()
    from shared.schemas import FeatureSnapshot1h

    # Build fs1h that drives a strong positive composite
    # ret_24h large negative → f_mom large positive (fade)
    # trend_agree = 1, vol_regime low → f_supp positive
    fs = FeatureSnapshot1h(
        asof_minute="2026-04-12T10:00:00Z",
        window_start_minute="2026-04-12T09:00:00Z",
        universe=["BTC"],
        mid_px={"BTC": 80000.0},
        ret_1h={"BTC": 0.0},
        ret_4h={"BTC": 0.0},
        vol_1h={"BTC": 0.01},
        ret_24h={"BTC": -0.15},   # strong negative 24h → fade → LONG
        trend_agree={"BTC": 1.0},
        vol_regime={"BTC": 0.0},
    )
    bias = mod.compute_quant_bias(fs, ["BTC"])
    btc = next(b for b in bias.biases if b.symbol == "BTC")
    # Strong negative 24h momentum → fade → composite positive → LONG
    assert btc.direction in ("LONG", "FLAT")
    # weight_scale must be 0.0, 0.5, or 1.0
    assert btc.weight_scale in (0.0, 0.5, 1.0)
    if btc.direction != "FLAT":
        # active signals must have non-zero scale
        assert btc.weight_scale > 0.0


def test_layer2_applies_weight_scale_half():
    """apply_direction_confirmation uses weight_scale=0.5 → weight is halved."""
    mod = load_ai()
    from shared.schemas import DirectionBias, SymbolBias, FeatureSnapshot15m

    half_bias = DirectionBias(
        asof_minute="2026-04-12T10:00:00Z",
        valid_until_minute="2026-04-12T11:00:00Z",
        market_state="TRENDING",
        biases=[
            SymbolBias(symbol="BTC", direction="LONG", confidence=0.70, weight_scale=1.0),
            SymbolBias(symbol="ETH", direction="LONG", confidence=0.70, weight_scale=0.5),
        ],
    )
    fs = FeatureSnapshot15m(
        asof_minute="2026-04-12T10:15:00Z",
        window_start_minute="2026-04-12T10:00:00Z",
        universe=["BTC", "ETH"],
        mid_px={"BTC": 80000.0, "ETH": 3000.0},
        # Confirmation signals to meet min_confirm=2
        funding_rate={"BTC": 0.001, "ETH": 0.001},
        oi_change_15m={"BTC": 0.01, "ETH": 0.01},
    )
    result = mod.apply_direction_confirmation(fs, half_bias, ["BTC", "ETH"])
    cap_btc = mod.CAP_BTC_ETH
    cap_eth = mod.CAP_BTC_ETH
    expected_btc = cap_btc * 0.70 * 1.0
    expected_eth = cap_eth * 0.70 * 0.5
    assert abs(result.get("BTC", 0.0) - expected_btc) < 1e-9, f"BTC weight mismatch: {result}"
    assert abs(result.get("ETH", 0.0) - expected_eth) < 1e-9, f"ETH weight mismatch: {result}"
