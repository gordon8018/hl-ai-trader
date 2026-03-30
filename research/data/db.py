"""DuckDB query layer over Parquet files."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import duckdb
import polars as pl


class ResearchDB:
    """Lightweight DuckDB wrapper for querying Parquet feature stores."""

    _VIEW_DIRS = ["features_1m", "features_15m", "features_1h", "trades", "snapshots"]

    def __init__(self, data_dir: str | Path, db_path: Optional[str | Path] = None):
        self.data_dir = Path(data_dir)
        db_file = str(db_path) if db_path else ":memory:"
        self.conn = duckdb.connect(db_file)

    def setup_views(self) -> None:
        """Create or replace views for each Parquet subdirectory."""
        for subdir in self._VIEW_DIRS:
            parquet_path = self.data_dir / subdir
            if parquet_path.exists() and any(parquet_path.glob("*.parquet")):
                glob_pattern = str(parquet_path / "*.parquet")
                self.conn.execute(f"""
                    CREATE OR REPLACE VIEW {subdir}
                    AS SELECT * FROM read_parquet('{glob_pattern}')
                """)

    def query(self, sql: str) -> pl.DataFrame:
        """Execute SQL and return result as Polars DataFrame."""
        return self.conn.execute(sql).pl()

    def close(self) -> None:
        self.conn.close()
