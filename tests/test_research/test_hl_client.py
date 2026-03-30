"""Tests for Hyperliquid client and data download."""
import polars as pl
import pytest
from unittest.mock import MagicMock, patch
from datetime import date


def test_candles_to_dataframe():
    """Test converting raw candle response to DataFrame."""
    from research.scripts.download_history import download_candles
    from research.data.hl_client import HLClient

    mock_client = MagicMock(spec=HLClient)
    mock_client.candles_range.return_value = [
        {"t": 1711000000000, "T": 1711000899999, "o": "65000.0", "h": "65100.0",
         "l": "64900.0", "c": "65050.0", "v": "1.5", "n": 50, "i": "15m", "s": "BTC"},
        {"t": 1711000900000, "T": 1711001799999, "o": "65050.0", "h": "65200.0",
         "l": "65000.0", "c": "65150.0", "v": "2.0", "n": 75, "i": "15m", "s": "BTC"},
    ]

    df = download_candles(mock_client, "BTC", date(2024, 3, 21), date(2024, 3, 21))
    assert len(df) == 2
    assert "close" in df.columns
    assert "volume" in df.columns
    assert df["close"][0] == 65050.0


def test_compute_features_basic():
    """Test feature computation from candle data."""
    from research.scripts.download_history import compute_features

    # Create 20 bars of synthetic data
    rows = []
    for i in range(20):
        rows.append({
            "ts": f"2026-03-15T{10 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
            "symbol": "BTC",
            "open": 100.0 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "close": 100.0 + i * 0.1,
            "volume": 1.0 + i * 0.01,
            "trade_count": 50 + i,
        })

    candle_df = pl.DataFrame(rows)
    funding_df = pl.DataFrame()

    features = compute_features(candle_df, funding_df)
    assert "mid_px" in features.columns
    assert "ret_15m" in features.columns
    assert "vol_15m" in features.columns
    assert "trend_15m" in features.columns
    assert "spread_bps" in features.columns
    assert len(features) == 20


def test_compute_features_with_funding():
    """Test that funding rate is joined correctly."""
    from research.scripts.download_history import compute_features

    candle_df = pl.DataFrame({
        "ts": ["2026-03-15T10:00:00Z", "2026-03-15T10:15:00Z", "2026-03-15T10:30:00Z"],
        "symbol": ["BTC"] * 3,
        "open": [100.0, 100.1, 100.2],
        "high": [100.5, 100.6, 100.7],
        "low": [99.5, 99.6, 99.7],
        "close": [100.1, 100.2, 100.3],
        "volume": [1.0, 1.1, 1.2],
        "trade_count": [50, 60, 70],
    })

    funding_df = pl.DataFrame({
        "ts": ["2026-03-15T08:00:00Z"],
        "symbol": ["BTC"],
        "funding_rate": [0.0001],
        "premium": [0.0005],
    })

    features = compute_features(candle_df, funding_df)
    assert "funding_rate" in features.columns
    # Funding should be forward-filled from 08:00 to all 3 bars
    assert features["funding_rate"][0] == 0.0001
