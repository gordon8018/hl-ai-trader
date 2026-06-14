import importlib
import json
import os
import sys
import tempfile


def _make_test_config():
    """创建临时测试JSON配置文件，返回文件路径。"""
    cfg = {
        "active_version": "TEST",
        "versions": {
            "TEST": {
                "UNIVERSE": "BTC,ETH",
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
                "POSITION_MAX_AGE_MIN": 30, "POSITION_PROFIT_TARGET_BPS": 25.0,
                "AI_DECISION_HORIZON": "30m", "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "", "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "", "AI_LLM_MODEL": "", "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.1m", "STREAM_IN_1H": "md.features.1h",
                "LAYER1_POLL_BLOCK_MS": 0,
                "CONSUMER": "ai_1", "CONSUMER_1H": "ai_layer1_1",
                "MAX_RETRIES": 5, "ERROR_STREAK_THRESHOLD": 3,
                "MIN_NOTIONAL_USD": 50.0, "MAX_TRADES_PER_DAY": 30,
                "MAX_GROSS_TRENDING_HIGH": 0.65, "MAX_GROSS_TRENDING_MID": 0.45,
                "MAX_GROSS_SIDEWAYS": 0.00, "MAX_GROSS_VOLATILE": 0.20,
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
            }
        }
    }
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(cfg, tf)
    tf.close()
    return tf.name


def load_module():
    config_path = _make_test_config()
    os.environ["AI_CONFIG_PATH"] = config_path
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        return importlib.import_module(mod_name)
    finally:
        os.unlink(config_path)


def test_normalize_nonnegative():
    mod = load_module()
    weights = {"BTC": 2.0, "ETH": -1.0}
    norm = mod.normalize(weights)
    assert abs(sum(norm.values()) - 1.0) < 1e-9
    assert norm["ETH"] == 0.0


def test_normalize_zero():
    mod = load_module()
    norm = mod.normalize({"BTC": 0.0, "ETH": 0.0})
    assert norm["BTC"] == 0.0
    assert norm["ETH"] == 0.0


def test_confidence_from_signals_bounds():
    mod = load_module()
    c0 = mod.confidence_from_signals({"BTC": 0.0, "ETH": 0.0}, ["BTC", "ETH"])
    c1 = mod.confidence_from_signals({"BTC": 10.0, "ETH": 5.0}, ["BTC", "ETH"])
    assert 0.0 <= c0 <= 1.0
    assert 0.0 <= c1 <= 1.0
    assert c1 > c0


def test_apply_confidence_gating_scales_down():
    mod = load_module()
    w = {"BTC": 0.2, "ETH": 0.1}
    out = mod.apply_confidence_gating(w, confidence=0.2, min_confidence=0.4)
    assert out["BTC"] == 0.1
    assert out["ETH"] == 0.05


def test_ewma_smooth():
    mod = load_module()
    new_w = {"BTC": 0.3, "ETH": 0.0}
    prev_w = {"BTC": 0.1, "ETH": 0.2}
    out = mod.ewma_smooth(new_w, prev_w, alpha=0.5, universe=["BTC", "ETH"])
    assert out["BTC"] == 0.2
    assert out["ETH"] == 0.1


def test_apply_turnover_cap():
    mod = load_module()
    target = {"BTC": 0.4, "ETH": 0.0}
    current = {"BTC": 0.0, "ETH": 0.0}
    capped, before, after = mod.apply_turnover_cap(
        target, current, cap=0.1, universe=["BTC", "ETH"]
    )
    assert before == 0.4
    assert abs(after - 0.1) < 1e-9
    assert abs(capped["BTC"] - 0.1) < 1e-9


def test_scale_gross():
    mod = load_module()
    w = {"BTC": 0.3, "ETH": 0.3}
    out = mod.scale_gross(w, gross_cap=0.3)
    gross = abs(out["BTC"]) + abs(out["ETH"])
    assert abs(gross - 0.3) < 1e-9


class DummyBus:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, key):
        return self.payload.get(key)


def test_get_current_weights_from_state_snapshot():
    mod = load_module()
    bus = DummyBus(
        {
            mod.STATE_KEY: {
                "data": {
                    "equity_usd": 1000.0,
                    "positions": {
                        "BTC": {"qty": 0.01, "mark_px": 50000.0},
                        "ETH": {"qty": 0.0, "mark_px": 3000.0},
                    },
                }
            }
        }
    )
    w = mod.get_current_weights(bus, ["BTC", "ETH"])
    assert abs(w["BTC"] - 0.5) < 1e-9
    assert abs(w["ETH"] - 0.0) < 1e-9


def test_parse_llm_weights_strict_ok():
    mod = load_module()
    raw = '{"targets":[{"symbol":"BTC","weight":0.2},{"symbol":"ETH","weight":0.1}],"confidence":0.7,"rationale":"llm"}'
    w, c, r, cw = mod.parse_llm_weights_strict(raw, ["BTC", "ETH"], max_gross=0.4)
    assert abs(w["BTC"] - 0.2) < 1e-9
    assert abs(w["ETH"] - 0.1) < 1e-9
    assert c == 0.7
    assert r == "llm"
    assert cw is None


def test_parse_llm_weights_strict_invalid_raises():
    mod = load_module()
    bad = "not json"
    try:
        mod.parse_llm_weights_strict(bad, ["BTC", "ETH"], max_gross=0.4)
        assert False, "expected parse error"
    except Exception:
        assert True


def test_maybe_llm_candidate_weights_fallback():
    mod = load_module()
    w, c, r, cw, raw, err = mod.maybe_llm_candidate_weights(
        use_llm=True,
        llm_raw_response="bad json",
        universe=["BTC", "ETH"],
        max_gross=0.4,
    )
    assert w is None
    assert c is None
    assert r is None
    assert cw is None
    assert raw == "bad json"
    assert isinstance(err, str) and err


def test_build_user_payload_contains_exec_feedback():
    mod = load_module()
    fs = mod.FeatureSnapshot15m(
        asof_minute="2026-02-18T16:00:00Z",
        window_start_minute="2026-02-18T15:46:00Z",
        universe=["BTC", "ETH"],
        mid_px={"BTC": 50000.0, "ETH": 3000.0},
        ret_15m={"BTC": 0.01, "ETH": 0.005},
        ret_30m={"BTC": 0.02, "ETH": 0.01},
        ret_1h={"BTC": 0.03, "ETH": 0.02},
        vol_15m={"BTC": 0.01, "ETH": 0.015},
        vol_1h={"BTC": 0.02, "ETH": 0.025},
        funding_rate={"BTC": 0.0001, "ETH": -0.0001},
        basis_bps={"BTC": 2.0, "ETH": 1.0},
        open_interest={"BTC": 1000000.0, "ETH": 800000.0},
        oi_change_15m={"BTC": 0.02, "ETH": -0.01},
        spread_bps={"BTC": 1.0, "ETH": 1.2},
        book_imbalance_l1={"BTC": 0.1, "ETH": -0.1},
        book_imbalance_l5={"BTC": 0.05, "ETH": -0.05},
        top_depth_usd={"BTC": 200000.0, "ETH": 150000.0},
        microprice={"BTC": 50001.0, "ETH": 3000.5},
        liquidity_score={"BTC": 0.8, "ETH": 0.7},
        reject_rate_15m={"BTC": 0.2, "ETH": 0.0},
        p95_latency_ms_15m={"BTC": 300.0, "ETH": 200.0},
        slippage_bps_15m={"BTC": 1.5, "ETH": 1.0},
    )
    payload = mod.build_user_payload(
        fs, {"BTC": 0.1, "ETH": 0.1}, {"BTC": 0.1, "ETH": 0.1}
    )
    assert "execution_feedback_15m" in payload
    ef = payload["execution_feedback_15m"]
    assert abs(ef["reject_rate_avg"] - 0.1) < 1e-9
    assert ef["p95_latency_ms_avg"] == 250.0
    assert abs(ef["slippage_bps_avg"] - 1.25) < 1e-9


def test_layer1_poll_block_ms_is_clamped_to_positive():
    mod = load_module()
    assert mod.LAYER1_POLL_BLOCK_MS == 1


# ── N3: compute_dynamic_leverage ─────────────────────────────────────────────

def test_dynamic_leverage_low_vol_returns_tier_high():
    mod = load_module()
    # vol_regime avg = 0.2, below LOW_THRESHOLD (0.5) → LEVERAGE_TIER_HIGH (6.0)
    result = mod.compute_dynamic_leverage({"BTC": 0.2, "ETH": 0.2})
    assert result == mod.LEVERAGE_TIER_HIGH


def test_dynamic_leverage_high_vol_returns_tier_low():
    mod = load_module()
    # vol_regime avg = 2.0, >= HIGH_THRESHOLD (1.5) → LEVERAGE_TIER_LOW (3.0)
    result = mod.compute_dynamic_leverage({"BTC": 2.0, "ETH": 2.0})
    assert result == mod.LEVERAGE_TIER_LOW


def test_dynamic_leverage_mid_vol_returns_base():
    mod = load_module()
    # vol_regime avg = 1.0, between thresholds → POSITION_LEVERAGE (5.0)
    result = mod.compute_dynamic_leverage({"BTC": 1.0, "ETH": 1.0})
    assert result == mod.POSITION_LEVERAGE


def test_dynamic_leverage_empty_returns_base():
    mod = load_module()
    result = mod.compute_dynamic_leverage({})
    assert result == mod.POSITION_LEVERAGE


def test_dynamic_leverage_mixed_averages_correctly():
    mod = load_module()
    # BTC=0.0, ETH=3.0 → avg=1.5, exactly at HIGH_THRESHOLD → TIER_LOW
    result = mod.compute_dynamic_leverage({"BTC": 0.0, "ETH": 3.0})
    assert result == mod.LEVERAGE_TIER_LOW


# ── M3: funding rate overlay ───────────────────────────────────────────────────

def _fs15m(**kwargs):
    from shared.schemas import FeatureSnapshot15m
    base = {
        "asof_minute": "2026-04-12T10:00:00Z",
        "window_start_minute": "2026-04-12T09:45:00Z",
        "universe": ["BTC", "ETH"],
        "mid_px": {"BTC": 80000.0, "ETH": 2000.0},
    }
    base.update(kwargs)
    return FeatureSnapshot15m(**base)


def test_funding_overlay_scales_down_long_when_high_positive_rate():
    mod = load_module()
    weights = {"BTC": 0.20, "ETH": 0.10}
    fs = _fs15m(funding_rate={"BTC": 0.001, "ETH": 0.0001})  # BTC > threshold, ETH below
    result = mod.apply_funding_overlay(weights, fs)
    # BTC long + high positive funding → scale down
    assert result["BTC"] < weights["BTC"]
    assert abs(result["BTC"] - weights["BTC"] * mod.FUNDING_OVERLAY_SCALE) < 1e-9
    # ETH below threshold → unchanged
    assert result["ETH"] == weights["ETH"]


def test_funding_overlay_scales_down_short_when_high_negative_rate():
    mod = load_module()
    weights = {"BTC": -0.15}
    fs = _fs15m(universe=["BTC"], mid_px={"BTC": 80000.0}, funding_rate={"BTC": -0.001})
    result = mod.apply_funding_overlay(weights, fs)
    assert result["BTC"] > weights["BTC"]  # less negative = scaled toward zero
    assert abs(result["BTC"] - weights["BTC"] * mod.FUNDING_OVERLAY_SCALE) < 1e-9


def test_funding_overlay_no_change_when_below_threshold():
    mod = load_module()
    weights = {"BTC": 0.20}
    fs = _fs15m(universe=["BTC"], mid_px={"BTC": 80000.0}, funding_rate={"BTC": 0.0001})
    result = mod.apply_funding_overlay(weights, fs)
    assert result["BTC"] == weights["BTC"]


# ── L2: per-symbol SOL cap ─────────────────────────────────────────────────────

def test_apply_symbol_caps_sol_override():
    mod = load_module()
    weights = {"BTC": 0.30, "ETH": 0.30, "SOL": 0.20}
    universe = ["BTC", "ETH", "SOL"]
    # SOL cap = 0.10 overrides CAP_ALT=0.15
    result = mod.apply_symbol_caps(weights, universe, cap_btc_eth=0.30, cap_alt=0.15, cap_sol=0.10)
    assert result["BTC"] == 0.30
    assert result["ETH"] == 0.30
    assert abs(result["SOL"] - 0.10) < 1e-9


def test_apply_symbol_caps_sol_falls_back_to_alt_when_zero():
    mod = load_module()
    weights = {"SOL": 0.20}
    result = mod.apply_symbol_caps(weights, ["SOL"], cap_btc_eth=0.30, cap_alt=0.15, cap_sol=0.0)
    # cap_sol=0.0 → inherit CAP_ALT
    assert abs(result["SOL"] - 0.15) < 1e-9


# ── L4: night low-liquidity protection ────────────────────────────────────────

def test_select_max_gross_night_protection_applies_during_protected_hours():
    """During UTC 16-20, select_max_gross should scale by NIGHT_PROTECT_SCALE."""
    mod = load_module()
    from unittest.mock import patch
    from datetime import datetime, timezone
    # Simulate 17:00 UTC (in NIGHT_PROTECT_HOURS = {16,17,18,19,20})
    fixed_dt = datetime(2026, 4, 12, 17, 0, 0, tzinfo=timezone.utc)
    with patch("services.ai_decision.app.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_dt
        mock_dt.fromisoformat = datetime.fromisoformat
        gross = mod.select_max_gross("TRENDING", 0.60)
    base = mod.MAX_GROSS_TRENDING_MID
    assert abs(gross - base * mod.NIGHT_PROTECT_SCALE) < 1e-9


def test_select_max_gross_no_night_protection_outside_hours():
    """Outside UTC 16-20, select_max_gross returns normal value."""
    mod = load_module()
    from unittest.mock import patch
    from datetime import datetime, timezone
    # Simulate 10:00 UTC (not in NIGHT_PROTECT_HOURS)
    fixed_dt = datetime(2026, 4, 12, 10, 0, 0, tzinfo=timezone.utc)
    with patch("services.ai_decision.app.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_dt
        mock_dt.fromisoformat = datetime.fromisoformat
        gross = mod.select_max_gross("TRENDING", 0.60)
    assert abs(gross - mod.MAX_GROSS_TRENDING_MID) < 1e-9
