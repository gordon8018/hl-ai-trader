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
