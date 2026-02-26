import importlib
import os
import sys


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


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
    capped, before, after = mod.apply_turnover_cap(target, current, cap=0.1, universe=["BTC", "ETH"])
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
    w, c, r = mod.parse_llm_weights_strict(raw, ["BTC", "ETH"], max_gross=0.4)
    assert abs(w["BTC"] - 0.2) < 1e-9
    assert abs(w["ETH"] - 0.1) < 1e-9
    assert c == 0.7
    assert r == "llm"


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
    w, c, r, raw, err = mod.maybe_llm_candidate_weights(
        use_llm=True,
        llm_raw_response="bad json",
        universe=["BTC", "ETH"],
        max_gross=0.4,
    )
    assert w is None
    assert c is None
    assert r is None
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
    payload = mod.build_user_payload(fs, {"BTC": 0.1, "ETH": 0.1}, {"BTC": 0.1, "ETH": 0.1})
    assert "execution_feedback_15m" in payload
    ef = payload["execution_feedback_15m"]
    assert abs(ef["reject_rate_avg"] - 0.1) < 1e-9
    assert ef["p95_latency_ms_avg"] == 250.0
    assert abs(ef["slippage_bps_avg"] - 1.25) < 1e-9
