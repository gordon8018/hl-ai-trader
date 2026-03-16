# tests/test_ai_decision_layer1.py
import os
import sys
import importlib
import json

def load_ai():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE")
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
