# tests/test_schemas_1h.py
import pytest
from shared.schemas import FeatureSnapshot1h, DirectionBias, SymbolBias


def test_feature_snapshot_1h_roundtrip():
    fs = FeatureSnapshot1h(
        asof_minute="2026-03-16T15:00:00Z",
        window_start_minute="2026-03-16T14:00:00Z",
        universe=["BTC", "ETH"],
        mid_px={"BTC": 70000.0, "ETH": 2100.0},
        ret_1h={"BTC": 0.005, "ETH": 0.003},
        ret_4h={"BTC": 0.012, "ETH": 0.008},
        vol_1h={"BTC": 0.01, "ETH": 0.012},
        vol_4h={"BTC": 0.02, "ETH": 0.022},
        trend_1h={"BTC": 1.0, "ETH": 1.0},
        trend_4h={"BTC": 1.0, "ETH": 0.0},
        rsi_14_1h={"BTC": 58.0, "ETH": 52.0},
        aggr_delta_1h={"BTC": 1500.0, "ETH": 300.0},
    )
    dumped = fs.model_dump()
    fs2 = FeatureSnapshot1h(**dumped)
    assert fs2.asof_minute == fs.asof_minute
    assert fs2.ret_4h["BTC"] == 0.012


def test_direction_bias_valid():
    db = DirectionBias(
        asof_minute="2026-03-16T15:00:00Z",
        valid_until_minute="2026-03-16T16:00:00Z",
        market_state="TRENDING",
        biases=[
            SymbolBias(symbol="BTC", direction="LONG", confidence=0.72),
            SymbolBias(symbol="ETH", direction="FLAT", confidence=0.40),
        ],
    )
    assert db.biases[0].direction == "LONG"
    assert db.biases[1].confidence == 0.40


def test_direction_bias_confidence_bounds():
    with pytest.raises(Exception):
        SymbolBias(symbol="BTC", direction="LONG", confidence=1.5)
    with pytest.raises(Exception):
        SymbolBias(symbol="BTC", direction="LONG", confidence=-0.1)


def test_direction_bias_invalid_direction():
    with pytest.raises(Exception):
        SymbolBias(symbol="BTC", direction="MAYBE", confidence=0.5)
