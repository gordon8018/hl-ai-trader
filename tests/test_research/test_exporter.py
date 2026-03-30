"""Tests for research/data/exporter.py"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.data.exporter import DataExporter, build_forward_returns, pivot_snapshot_to_rows
from tests.fake_redis import FakeRedis


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


# ---------------------------------------------------------------------------
# DataExporter.export_redis_stream
# ---------------------------------------------------------------------------

def test_export_redis_stream_creates_parquet():
    redis = FakeRedis()
    stream_key = "md.features.1m"

    snapshot = {
        "asof_minute": "2024-01-15T10:00:00Z",
        "universe": ["BTC", "ETH"],
        "mid_px": {"BTC": 42000.0, "ETH": 2200.0},
    }
    # Use a timestamp-like ID (ms since epoch for 2024-01-15)
    redis.streams[stream_key] = [
        ("1705312800000-0", {"data": json.dumps(snapshot)}),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = DataExporter(redis_client=redis, sqlite_path=None, output_dir=tmpdir)
        written = exporter.export_redis_stream(
            stream_key,
            date_range=(date(2024, 1, 15), date(2024, 1, 15)),
        )

        assert len(written) == 1
        out_path = written[0]
        assert out_path.exists()
        assert out_path.suffix == ".parquet"
        assert "2024-01-15" in out_path.name

        df = pl.read_parquet(out_path)
        assert set(df["symbol"].to_list()) == {"BTC", "ETH"}
        assert "mid_px" in df.columns


def test_export_redis_stream_skips_existing():
    redis = FakeRedis()
    stream_key = "md.features.1m"

    snapshot = {
        "asof_minute": "2024-01-15T10:00:00Z",
        "universe": ["BTC"],
        "mid_px": {"BTC": 42000.0},
    }
    redis.streams[stream_key] = [
        ("1705312800000-0", {"data": json.dumps(snapshot)}),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = DataExporter(redis_client=redis, sqlite_path=None, output_dir=tmpdir)

        # First export
        written1 = exporter.export_redis_stream(
            stream_key,
            date_range=(date(2024, 1, 15), date(2024, 1, 15)),
        )
        assert len(written1) == 1

        # Second export — file exists, should be skipped
        written2 = exporter.export_redis_stream(
            stream_key,
            date_range=(date(2024, 1, 15), date(2024, 1, 15)),
        )
        assert len(written2) == 0


# ---------------------------------------------------------------------------
# DataExporter.export_sqlite_table
# ---------------------------------------------------------------------------

def test_export_sqlite_table():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "trading.db"

        # Create a minimal SQLite DB with exec_reports table
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE exec_reports (ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL)"
        )
        conn.executemany(
            "INSERT INTO exec_reports VALUES (?, ?, ?, ?, ?)",
            [
                ("2024-01-15T10:00:00Z", "BTC", "buy", 0.1, 42000.0),
                ("2024-01-15T11:00:00Z", "ETH", "sell", 1.0, 2200.0),
                ("2024-01-16T09:00:00Z", "BTC", "sell", 0.05, 43000.0),
            ],
        )
        conn.commit()
        conn.close()

        out_dir = Path(tmpdir) / "output"
        exporter = DataExporter(redis_client=None, sqlite_path=db_path, output_dir=out_dir)
        written = exporter.export_sqlite_table(
            "exec_reports",
            date_range=(date(2024, 1, 15), date(2024, 1, 16)),
        )

        assert len(written) == 2
        dates_written = {p.stem for p in written}
        assert "2024-01-15" in dates_written
        assert "2024-01-16" in dates_written

        df_15 = pl.read_parquet(out_dir / "trades" / "2024-01-15.parquet")
        assert len(df_15) == 2
        assert set(df_15["symbol"].to_list()) == {"BTC", "ETH"}


def test_export_sqlite_table_raises_without_path():
    exporter = DataExporter(redis_client=None, sqlite_path=None, output_dir="/tmp")
    with pytest.raises(ValueError, match="sqlite_path not configured"):
        exporter.export_sqlite_table("exec_reports", date_range=(date(2024, 1, 1), date(2024, 1, 1)))


# ---------------------------------------------------------------------------
# build_forward_returns
# ---------------------------------------------------------------------------

def test_build_forward_returns():
    df = pl.DataFrame(
        {
            "symbol": ["BTC", "BTC", "BTC", "ETH", "ETH", "ETH"],
            "ts": [
                "2024-01-15T10:00:00Z",
                "2024-01-15T10:15:00Z",
                "2024-01-15T10:30:00Z",
                "2024-01-15T10:00:00Z",
                "2024-01-15T10:15:00Z",
                "2024-01-15T10:30:00Z",
            ],
            "mid_px": [100.0, 101.0, 102.0, 200.0, 202.0, 198.0],
        }
    )

    result = build_forward_returns(df, horizons=[1, 2])

    assert "ret_fwd_15m" in result.columns
    assert "ret_fwd_30m" in result.columns

    # BTC bar0: fwd_15m = (101 - 100) / 100 = 0.01
    btc = result.filter(pl.col("symbol") == "BTC").sort("ts")
    assert abs(btc["ret_fwd_15m"][0] - 0.01) < 1e-9
    # BTC bar0: fwd_30m = (102 - 100) / 100 = 0.02
    assert abs(btc["ret_fwd_30m"][0] - 0.02) < 1e-9

    # Last bar of each symbol should be null (no future data)
    assert btc["ret_fwd_15m"][-1] is None

    # ETH bar0: fwd_15m = (202 - 200) / 200 = 0.01
    eth = result.filter(pl.col("symbol") == "ETH").sort("ts")
    assert abs(eth["ret_fwd_15m"][0] - 0.01) < 1e-9
