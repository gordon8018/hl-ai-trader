import importlib
import json
import os
import sys
import tempfile
import types


def _make_test_config():
    cfg = {
        "active_version": "TEST",
        "versions": {
            "TEST": {
                "UNIVERSE": "BTC,ETH",
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
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70,
            }
        },
    }
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(cfg, tf)
    tf.close()
    return tf.name


def load_module():
    config_path = _make_test_config()
    original_ai_config_path = os.environ.get("AI_CONFIG_PATH")
    original_modules = {
        name: sys.modules.get(name)
        for name in ("redis", "redis.exceptions", "prometheus_client", "services.ai_decision.app")
    }
    os.environ["AI_CONFIG_PATH"] = config_path
    redis_stub = types.ModuleType("redis")

    class _RedisConnectionPool:
        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

    class _RedisClient:
        def __init__(self, *args, **kwargs):
            pass

    redis_stub.ConnectionPool = _RedisConnectionPool
    redis_stub.Redis = _RedisClient
    redis_exceptions = types.ModuleType("redis.exceptions")

    class _RedisError(Exception):
        pass

    redis_exceptions.ResponseError = _RedisError
    redis_exceptions.TimeoutError = _RedisError
    redis_exceptions.ConnectionError = _RedisError
    sys.modules["redis"] = redis_stub
    sys.modules["redis.exceptions"] = redis_exceptions
    prom_stub = types.ModuleType("prometheus_client")

    class _Metric:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            return None

        def observe(self, *args, **kwargs):
            return None

    prom_stub.Counter = _Metric
    prom_stub.Gauge = _Metric
    prom_stub.Histogram = _Metric
    prom_stub.start_http_server = lambda *args, **kwargs: None
    sys.modules["prometheus_client"] = prom_stub
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    mod = None
    try:
        mod = importlib.import_module(mod_name)
        return mod
    finally:
        os.unlink(config_path)
        if original_ai_config_path is None:
            os.environ.pop("AI_CONFIG_PATH", None)
        else:
            os.environ["AI_CONFIG_PATH"] = original_ai_config_path
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class DummyBus:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def get_json(self, key):
        return self.payload.get(key)


def test_should_rebalance_uses_current_weights_not_prev_weights(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "AI_SIGNAL_DELTA_THRESHOLD", 0.01)

    bus = DummyBus()
    prev_w = {"BTC": 0.0, "ETH": 0.0}
    current_w = {"BTC": -0.02, "ETH": 0.01}
    candidate_w = {"BTC": -0.019, "ETH": 0.009}

    rebalance, delta, reason = mod.should_rebalance(
        bus=bus,
        prev_w=prev_w,
        current_w=current_w,
        candidate_w=candidate_w,
        asof_minute="2026-03-27T03:05:00Z",
    )

    assert rebalance is False
    assert abs(delta - 0.002) < 1e-9
    assert reason == "signal_delta_below_threshold_vs_current"


def test_should_rebalance_returns_major_rebalance_when_current_baseline_diff_exceeds_threshold(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "AI_SIGNAL_DELTA_THRESHOLD", 0.01)

    bus = DummyBus()
    prev_w = {"BTC": 0.0, "ETH": 0.0}
    current_w = {"BTC": -0.02, "ETH": 0.01}
    candidate_w = {"BTC": 0.03, "ETH": -0.02}

    rebalance, delta, reason = mod.should_rebalance(
        bus=bus,
        prev_w=prev_w,
        current_w=current_w,
        candidate_w=candidate_w,
        asof_minute="2026-03-27T03:05:00Z",
    )

    assert rebalance is True
    assert delta > 0.01
    assert reason == "major_rebalance"


def test_position_target_mismatch_detects_current_nonzero_prev_zero():
    mod = load_module()

    mismatch, current_gross, prev_gross = mod.detect_position_target_mismatch(
        current_w={"BTC": -0.02, "ETH": 0.01},
        prev_w={"BTC": 0.0, "ETH": 0.0},
        epsilon=0.002,
    )

    assert mismatch is True
    assert current_gross > 0.0
    assert prev_gross == 0.0
