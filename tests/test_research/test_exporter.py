"""Tests for research/data/exporter.py"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.data.exporter import pivot_snapshot_to_rows


# ---------------------------------------------------------------------------
# pivot_snapshot_to_rows
# ---------------------------------------------------------------------------

def test_pivot_snapshot_to_rows():
    snapshot = {
        "asof_minute": "2024-01-15T10:00:00Z",
        "universe": ["BTC", "ETH"],
        "mid_px": {"BTC": 42000.0, "ETH": 2200.0},
        "funding_rate": {"BTC": 0.0001, "ETH": 0.0002},
    }
    rows = pivot_snapshot_to_rows(snapshot)
    assert len(rows) == 2

    btc_row = next(r for r in rows if r["symbol"] == "BTC")
    assert btc_row["ts"] == "2024-01-15T10:00:00Z"
    assert btc_row["mid_px"] == 42000.0
    assert btc_row["funding_rate"] == 0.0001

    eth_row = next(r for r in rows if r["symbol"] == "ETH")
    assert eth_row["mid_px"] == 2200.0
    assert eth_row["funding_rate"] == 0.0002


def test_pivot_handles_missing_symbol_in_dict():
    snapshot = {
        "asof_minute": "2024-01-15T10:00:00Z",
        "universe": ["BTC", "ETH", "SOL"],
        "mid_px": {"BTC": 42000.0, "ETH": 2200.0},  # SOL missing
    }
    rows = pivot_snapshot_to_rows(snapshot)
    assert len(rows) == 3

    sol_row = next(r for r in rows if r["symbol"] == "SOL")
    assert sol_row["mid_px"] is None
