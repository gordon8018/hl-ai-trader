import polars as pl
import duckdb
import pytest
from pathlib import Path


def test_create_views_and_query(tmp_path):
    """DuckDB should create views over Parquet and allow SQL queries."""
    from research.data.db import ResearchDB

    parquet_dir = tmp_path / "features_15m"
    parquet_dir.mkdir()
    df = pl.DataFrame({
        "ts": ["2026-03-15T10:00:00Z", "2026-03-15T10:00:00Z"],
        "symbol": ["BTC", "ETH"],
        "mid_px": [65000.0, 3200.0],
        "book_imbalance_l5": [0.12, -0.05],
        "ret_fwd_15m": [0.003, -0.001],
    })
    df.write_parquet(parquet_dir / "2026-03-15.parquet")

    db = ResearchDB(data_dir=tmp_path, db_path=tmp_path / "test.duckdb")
    db.setup_views()

    result = db.query("SELECT symbol, avg(book_imbalance_l5) as avg_bi FROM features_15m GROUP BY symbol ORDER BY symbol")
    assert len(result) == 2
    assert result["symbol"][0] == "BTC"
    assert abs(result["avg_bi"][0] - 0.12) < 1e-9
