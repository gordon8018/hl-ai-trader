# tests/test_market_data_1h.py
import importlib
import os
import sys
from collections import deque

def load_md():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.market_data.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def make_hist(prices_with_offsets_minutes: list, now_ts: float) -> deque:
    """Build a deque of (ts, px) from list of (minutes_ago, price)."""
    h = deque(maxlen=500)
    for mins_ago, px in sorted(prices_with_offsets_minutes, reverse=True):
        h.appendleft((now_ts - mins_ago * 60, px))
    return h


def test_rsi_from_hist_neutral():
    mod = load_md()
    import time
    now = time.time()
    # Flat price → RSI ~50
    hist = make_hist([(i, 100.0) for i in range(20, 0, -1)], now)
    hist.append((now, 100.0))
    rsi = mod.rsi_from_hist(hist, now, period=14)
    assert 45.0 <= rsi <= 55.0


def test_compute_1h_features_returns_expected_keys():
    mod = load_md()
    import time
    now = time.time()
    # Create 5h of price history at 100 + slow uptrend
    prices = [(i, 100.0 + (300 - i) * 0.01) for i in range(300, 0, -1)]
    hist = make_hist(prices, now)
    hist.append((now, 103.0))

    result = mod.compute_1h_features("BTC", hist, now)
    assert "ret_1h" in result
    assert "ret_4h" in result
    assert "vol_1h" in result
    assert "vol_4h" in result
    assert "trend_1h" in result
    assert "trend_4h" in result
    assert "rsi_14_1h" in result
    assert "aggr_delta_1h" in result


def test_compute_1h_features_uptrend():
    mod = load_md()
    import time
    now = time.time()
    # Strong uptrend: price rose 2% over 4 hours
    prices = [(i, 100.0 + (300 - i) * (2.0 / 300)) for i in range(300, 0, -1)]
    hist = make_hist(prices, now)
    hist.append((now, 102.0))
    result = mod.compute_1h_features("BTC", hist, now)
    assert result["ret_4h"] > 0.01   # ~2% over 4h
    assert result["trend_1h"] == 1   # uptrend
    assert result["trend_4h"] == 1
