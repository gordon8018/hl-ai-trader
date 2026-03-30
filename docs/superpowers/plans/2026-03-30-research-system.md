# Research System Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent research system for factor evaluation, backtesting, and parameter optimization for the hl-ai-trader crypto trading system.

**Architecture:** Three-phase pipeline: (1) Data export from Redis/SQLite to Parquet with DuckDB query layer, (2) Factor evaluation with IC/ICIR analysis and event-driven backtesting, (3) LLM-driven parameter optimization reused from feature_factory. All code lives in `research/` directory, completely isolated from production services.

**Tech Stack:** Python 3.10+, Polars (DataFrames), DuckDB (query layer), Parquet (storage), Pydantic (schema reuse from `shared/schemas.py`), pytest, matplotlib/plotly (reports)

**Spec:** `docs/superpowers/specs/2026-03-30-research-system-design.md`

---

## Chunk 1: Project Setup & Data Export Layer

### Task 1: Project scaffolding and dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `research/__init__.py`
- Create: `research/data/__init__.py`
- Create: `research/factors/__init__.py`
- Create: `research/backtest/__init__.py`
- Create: `research/optimizer/__init__.py`
- Create: `research/scripts/__init__.py`

- [ ] **Step 1: Add research dependencies to pyproject.toml**

Add an optional dependency group `research` in `pyproject.toml`:

```python
[project.optional-dependencies]
dev = [
  "pytest>=8.0.0",
]
research = [
  "polars>=1.0.0",
  "duckdb>=1.0.0",
  "pyarrow>=15.0.0",
  "scipy>=1.12.0",
  "numpy>=1.26.0",
  "matplotlib>=3.8.0",
  "plotly>=5.18.0",
]
```

Also add `"research"` to `[tool.setuptools] packages`:

```toml
[tool.setuptools]
packages = ["services", "shared", "research"]
```

- [ ] **Step 2: Create directory structure with __init__.py files**

Create empty `__init__.py` in each directory:
- `research/__init__.py`
- `research/data/__init__.py`
- `research/factors/__init__.py`
- `research/backtest/__init__.py`
- `research/optimizer/__init__.py`
- `research/scripts/__init__.py`
- `tests/test_research/__init__.py`

- [ ] **Step 3: Create data output directories**

```bash
mkdir -p research/data/parquet/{features_1m,features_15m,features_1h,trades,snapshots}
mkdir -p research/data/duckdb
mkdir -p research/reports/{factor_eval,backtest,optimizer}
```

Add `.gitkeep` files in each empty directory. Add to `.gitignore`:

```
research/data/parquet/**/*.parquet
research/data/duckdb/*.duckdb
research/reports/**/*.csv
research/reports/**/*.png
research/reports/**/*.json
```

- [ ] **Step 4: Install research dependencies**

Run: `pip install -e ".[research]"`
Expected: All packages install successfully

- [ ] **Step 5: Commit**

```bash
git add research/ pyproject.toml .gitignore
git commit -m "feat(research): scaffold project structure and dependencies"
```

---

### Task 2: Data exporter — Redis Stream to Parquet

**Files:**
- Create: `research/data/exporter.py`
- Create: `tests/test_research/test_exporter.py`

**Context:**
- Redis Streams store FeatureSnapshot as JSON-serialized Pydantic models
- Each message has fields as `Dict[str, float]` (per-symbol dicts)
- Must pivot to panel format: one row per (timestamp, symbol)
- Schema classes: `FeatureSnapshot1m`, `FeatureSnapshot15m`, `FeatureSnapshot1h` from `shared/schemas.py`
- Stream keys: `md.features.1m`, `md.features.15m`, `md.features.1h`

- [ ] **Step 1: Write test for snapshot-to-rows pivot logic**

```python
# tests/test_research/test_exporter.py
import polars as pl
import pytest
from datetime import datetime

def test_pivot_snapshot_to_rows():
    """A single FeatureSnapshot15m message should become N rows (one per symbol)."""
    from research.data.exporter import pivot_snapshot_to_rows

    snapshot_data = {
        "asof_minute": "2026-03-15T10:00:00Z",
        "window_start_minute": "2026-03-15T09:45:00Z",
        "universe": ["BTC", "ETH"],
        "mid_px": {"BTC": 65000.0, "ETH": 3200.0},
        "ret_15m": {"BTC": 0.003, "ETH": -0.001},
        "book_imbalance_l5": {"BTC": 0.12, "ETH": -0.05},
        "funding_rate": {"BTC": 0.0001, "ETH": 0.00015},
        "spread_bps": {"BTC": 1.2, "ETH": 2.3},
    }

    rows = pivot_snapshot_to_rows(snapshot_data)

    assert len(rows) == 2
    btc_row = [r for r in rows if r["symbol"] == "BTC"][0]
    assert btc_row["ts"] == "2026-03-15T10:00:00Z"
    assert btc_row["mid_px"] == 65000.0
    assert btc_row["ret_15m"] == 0.003
    assert btc_row["book_imbalance_l5"] == 0.12

    eth_row = [r for r in rows if r["symbol"] == "ETH"][0]
    assert eth_row["ret_15m"] == -0.001


def test_pivot_handles_missing_symbol_in_dict():
    """If a dict field is missing a symbol, use None."""
    from research.data.exporter import pivot_snapshot_to_rows

    snapshot_data = {
        "asof_minute": "2026-03-15T10:00:00Z",
        "universe": ["BTC", "ETH"],
        "mid_px": {"BTC": 65000.0, "ETH": 3200.0},
        "ret_15m": {"BTC": 0.003},  # ETH missing
    }

    rows = pivot_snapshot_to_rows(snapshot_data)
    eth_row = [r for r in rows if r["symbol"] == "ETH"][0]
    assert eth_row["ret_15m"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_exporter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.data.exporter'`

- [ ] **Step 3: Implement pivot_snapshot_to_rows**

```python
# research/data/exporter.py
"""Export data from Redis Streams and SQLite to Parquet files."""
from __future__ import annotations

import json
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import polars as pl


# Fields that are metadata, not per-symbol dicts
_META_FIELDS = {"asof_minute", "window_start_minute", "universe", "next_funding_ts"}


def pivot_snapshot_to_rows(snapshot_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a FeatureSnapshot message (with Dict[str, float] fields) to flat rows.

    Each symbol in 'universe' becomes one row. Dict fields are unpacked to scalars.
    Missing symbols in a dict field get None.
    """
    universe = snapshot_data.get("universe", [])
    ts = snapshot_data.get("asof_minute", "")

    rows = []
    for symbol in universe:
        row: dict[str, Any] = {"ts": ts, "symbol": symbol}
        for key, value in snapshot_data.items():
            if key in _META_FIELDS:
                continue
            if isinstance(value, dict):
                row[key] = value.get(symbol)
            # skip non-dict, non-meta fields (like lists)
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_exporter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/data/exporter.py tests/test_research/test_exporter.py
git commit -m "feat(research): add pivot_snapshot_to_rows for Dict→panel conversion"
```

- [ ] **Step 6: Write test for Redis stream export**

```python
# tests/test_research/test_exporter.py (append)

def test_export_redis_stream_creates_parquet(tmp_path, monkeypatch):
    """export_redis_stream should write daily Parquet files."""
    from research.data.exporter import DataExporter

    # Mock redis client
    class FakeRedis:
        def xrange(self, stream_key, min_id="-", max_id="+"):
            ts_ms = int(datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
            return [
                (f"{ts_ms}-0", {
                    b"data": json.dumps({
                        "asof_minute": "2026-03-15T10:00:00Z",
                        "universe": ["BTC", "ETH"],
                        "mid_px": {"BTC": 65000.0, "ETH": 3200.0},
                        "ret_15m": {"BTC": 0.003, "ETH": -0.001},
                    }).encode()
                }),
                (f"{ts_ms + 900000}-0", {
                    b"data": json.dumps({
                        "asof_minute": "2026-03-15T10:15:00Z",
                        "universe": ["BTC", "ETH"],
                        "mid_px": {"BTC": 65100.0, "ETH": 3210.0},
                        "ret_15m": {"BTC": 0.002, "ETH": 0.003},
                    }).encode()
                }),
            ]

    exporter = DataExporter(
        redis_client=FakeRedis(),
        sqlite_path=None,
        output_dir=tmp_path,
    )
    exporter.export_redis_stream(
        stream_key="md.features.15m",
        date_range=(date(2026, 3, 15), date(2026, 3, 15)),
    )

    parquet_path = tmp_path / "features_15m" / "2026-03-15.parquet"
    assert parquet_path.exists()

    df = pl.read_parquet(parquet_path)
    assert len(df) == 4  # 2 messages × 2 symbols
    assert set(df["symbol"].to_list()) == {"BTC", "ETH"}
    assert "mid_px" in df.columns
    assert "ret_15m" in df.columns
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_research/test_exporter.py::test_export_redis_stream_creates_parquet -v`
Expected: FAIL

- [ ] **Step 8: Implement DataExporter class with export_redis_stream**

```python
# research/data/exporter.py (add to existing file)

# Map stream keys to output subdirectories
_STREAM_DIR_MAP = {
    "md.features.1m": "features_1m",
    "md.features.15m": "features_15m",
    "md.features.1h": "features_1h",
}


class DataExporter:
    """Export historical data from Redis Streams and SQLite to Parquet."""

    def __init__(
        self,
        redis_client: Any,
        sqlite_path: Optional[str | Path],
        output_dir: str | Path,
    ):
        self.redis = redis_client
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        self.output_dir = Path(output_dir)

    def export_redis_stream(
        self,
        stream_key: str,
        date_range: tuple[date, date],
    ) -> list[Path]:
        """Export a Redis stream to daily Parquet files. Incremental: skips existing dates."""
        subdir = _STREAM_DIR_MAP.get(stream_key, stream_key.replace(".", "_"))
        out_dir = self.output_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)

        start_date, end_date = date_range
        # Convert date range to Redis stream IDs (ms timestamps)
        start_ms = int(datetime.combine(start_date, datetime.min.time(),
                                         tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime.combine(end_date, datetime.max.time().replace(microsecond=0),
                                       tzinfo=timezone.utc).timestamp() * 1000)

        # Fetch all messages in range
        messages = self.redis.xrange(stream_key, min_id=str(start_ms), max_id=str(end_ms))

        # Parse and pivot all messages
        all_rows: list[dict[str, Any]] = []
        for msg_id, fields in messages:
            data_bytes = fields.get(b"data") or fields.get("data")
            if not data_bytes:
                continue
            if isinstance(data_bytes, bytes):
                data_bytes = data_bytes.decode()
            snapshot = json.loads(data_bytes)
            all_rows.extend(pivot_snapshot_to_rows(snapshot))

        if not all_rows:
            return []

        # Build DataFrame and partition by date
        df = pl.DataFrame(all_rows)
        df = df.with_columns(
            pl.col("ts").str.slice(0, 10).alias("date")
        )

        written: list[Path] = []
        for dt_str, group_df in df.group_by("date"):
            dt_val = dt_str[0] if isinstance(dt_str, tuple) else dt_str
            out_path = out_dir / f"{dt_val}.parquet"
            if out_path.exists():
                continue  # incremental: skip existing
            group_df.drop("date").write_parquet(out_path)
            written.append(out_path)

        return written
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_research/test_exporter.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add research/data/exporter.py tests/test_research/test_exporter.py
git commit -m "feat(research): add DataExporter with Redis stream to Parquet export"
```

---

### Task 3: Data exporter — SQLite to Parquet

**Files:**
- Modify: `research/data/exporter.py`
- Modify: `tests/test_research/test_exporter.py`

**Context:**
- SQLite tables: `exec_reports` (columns: ts, cycle_id, symbol, status, filled_qty, avg_px, fee, latency_ms), `state_snapshots` (columns: ts, equity_usd, cash_usd, positions_json)
- See `services/reporting/app.py` for schema details

- [ ] **Step 1: Write test for SQLite export**

```python
# tests/test_research/test_exporter.py (append)
import sqlite3

def test_export_sqlite_table(tmp_path):
    """export_sqlite_table should write daily Parquet from SQLite."""
    from research.data.exporter import DataExporter

    # Create test SQLite DB
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE exec_reports (
            ts TEXT, cycle_id TEXT, symbol TEXT, status TEXT,
            filled_qty REAL, avg_px REAL, fee REAL, latency_ms INTEGER
        )
    """)
    conn.execute("""
        INSERT INTO exec_reports VALUES
        ('2026-03-15T10:00:00Z', 'cyc1', 'BTC', 'FILLED', 0.01, 65000.0, 0.23, 150)
    """)
    conn.execute("""
        INSERT INTO exec_reports VALUES
        ('2026-03-15T11:00:00Z', 'cyc2', 'ETH', 'FILLED', 0.5, 3200.0, 0.11, 200)
    """)
    conn.commit()
    conn.close()

    exporter = DataExporter(
        redis_client=None,
        sqlite_path=db_path,
        output_dir=tmp_path / "output",
    )
    exporter.export_sqlite_table(
        table="exec_reports",
        date_range=(date(2026, 3, 15), date(2026, 3, 15)),
    )

    parquet_path = tmp_path / "output" / "trades" / "2026-03-15.parquet"
    assert parquet_path.exists()

    df = pl.read_parquet(parquet_path)
    assert len(df) == 2
    assert "symbol" in df.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_exporter.py::test_export_sqlite_table -v`
Expected: FAIL

- [ ] **Step 3: Implement export_sqlite_table**

```python
# research/data/exporter.py (add to DataExporter class)

    # Map SQLite table names to output subdirectories
    _SQLITE_DIR_MAP = {
        "exec_reports": "trades",
        "state_snapshots": "snapshots",
    }

    def export_sqlite_table(
        self,
        table: str,
        date_range: tuple[date, date],
    ) -> list[Path]:
        """Export a SQLite table to daily Parquet files."""
        import sqlite3

        if self.sqlite_path is None:
            raise ValueError("sqlite_path not configured")

        subdir = self._SQLITE_DIR_MAP.get(table, table)
        out_dir = self.output_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)

        start_date, end_date = date_range
        start_str = start_date.isoformat()
        end_str = (end_date.isoformat()) + "T23:59:59Z"

        conn = sqlite3.connect(str(self.sqlite_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE ts >= ? AND ts <= ? ORDER BY ts",
            (start_str, end_str),
        ).fetchall()
        conn.close()

        if not rows:
            return []

        records = [dict(r) for r in rows]
        df = pl.DataFrame(records)
        df = df.with_columns(
            pl.col("ts").str.slice(0, 10).alias("date")
        )

        written: list[Path] = []
        for dt_str, group_df in df.group_by("date"):
            dt_val = dt_str[0] if isinstance(dt_str, tuple) else dt_str
            out_path = out_dir / f"{dt_val}.parquet"
            if out_path.exists():
                continue
            group_df.drop("date").write_parquet(out_path)
            written.append(out_path)

        return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_exporter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/data/exporter.py tests/test_research/test_exporter.py
git commit -m "feat(research): add SQLite to Parquet export"
```

---

### Task 4: Forward returns computation

**Files:**
- Modify: `research/data/exporter.py`
- Modify: `tests/test_research/test_exporter.py`

**Context:**
- Forward returns are computed on 15m bar data
- Horizons: [1, 2, 4, 8, 16] bars → 15m, 30m, 1h, 2h, 4h
- Formula: `ret_fwd_Nbar = (mid_px_{t+N} - mid_px_t) / mid_px_t`
- Must be computed per-symbol independently
- Only used as factor evaluation target, never as input features

- [ ] **Step 1: Write test for forward returns**

```python
# tests/test_research/test_exporter.py (append)

def test_build_forward_returns():
    """Forward returns should shift mid_px correctly per symbol."""
    from research.data.exporter import build_forward_returns

    df = pl.DataFrame({
        "ts": [f"2026-03-15T{10+i//4:02d}:{(i%4)*15:02d}:00Z" for i in range(8)] * 2,
        "symbol": ["BTC"] * 8 + ["ETH"] * 8,
        "mid_px": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0,
                    50.0, 50.5, 51.0, 51.5, 52.0, 52.5, 53.0, 53.5],
    })

    result = build_forward_returns(df, horizons=[1, 2, 4])

    assert "ret_fwd_15m" in result.columns  # horizon=1 bar = 15m
    assert "ret_fwd_30m" in result.columns  # horizon=2 bars = 30m
    assert "ret_fwd_1h" in result.columns   # horizon=4 bars = 1h

    # BTC row 0: mid_px=100, forward 1 bar mid_px=101 → ret = 0.01
    btc_rows = result.filter(pl.col("symbol") == "BTC").sort("ts")
    assert abs(btc_rows["ret_fwd_15m"][0] - 0.01) < 1e-9

    # BTC row 0: forward 4 bars mid_px=104 → ret = 0.04
    assert abs(btc_rows["ret_fwd_1h"][0] - 0.04) < 1e-9

    # Last rows should have null forward returns (no future data)
    assert btc_rows["ret_fwd_15m"][-1] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_exporter.py::test_build_forward_returns -v`
Expected: FAIL

- [ ] **Step 3: Implement build_forward_returns**

```python
# research/data/exporter.py (add as module-level function)

_HORIZON_NAMES = {1: "15m", 2: "30m", 4: "1h", 8: "2h", 16: "4h"}


def build_forward_returns(
    df: pl.DataFrame,
    horizons: Sequence[int] = (1, 2, 4, 8, 16),
) -> pl.DataFrame:
    """Compute forward returns per symbol.

    Args:
        df: Must have columns 'ts', 'symbol', 'mid_px'. Sorted by ts within each symbol.
        horizons: Number of bars forward. 1 bar = 15 minutes.

    Returns:
        DataFrame with added columns ret_fwd_15m, ret_fwd_30m, etc.
    """
    result = df.sort(["symbol", "ts"])
    for h in horizons:
        col_name = f"ret_fwd_{_HORIZON_NAMES.get(h, f'{h}bar')}"
        result = result.with_columns(
            ((pl.col("mid_px").shift(-h).over("symbol") - pl.col("mid_px"))
             / pl.col("mid_px")).alias(col_name)
        )
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_exporter.py::test_build_forward_returns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/data/exporter.py tests/test_research/test_exporter.py
git commit -m "feat(research): add forward returns computation aligned to trading horizons"
```

---

### Task 5: DuckDB query layer setup

**Files:**
- Create: `research/data/db.py`
- Create: `tests/test_research/test_db.py`

- [ ] **Step 1: Write test for DuckDB view creation**

```python
# tests/test_research/test_db.py
import polars as pl
import duckdb
import pytest
from pathlib import Path


def test_create_views_and_query(tmp_path):
    """DuckDB should create views over Parquet and allow SQL queries."""
    from research.data.db import ResearchDB

    # Create test parquet
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_db.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ResearchDB**

```python
# research/data/db.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/data/db.py tests/test_research/test_db.py
git commit -m "feat(research): add DuckDB query layer over Parquet files"
```

---

### Task 6: CLI script — export_data.py

**Files:**
- Create: `research/scripts/export_data.py`

**Context:**
- Entry point that ties together DataExporter
- CLI args: `--source redis,sqlite`, `--date-range 2026-03-01:2026-03-30`, `--redis-url`, `--sqlite-path`, `--output-dir`
- After export, compute forward returns on 15m data

- [ ] **Step 1: Implement export_data.py CLI**

```python
# research/scripts/export_data.py
"""CLI: Export historical data from Redis/SQLite to Parquet."""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path


def parse_date_range(s: str) -> tuple[date, date]:
    start_s, end_s = s.split(":")
    return date.fromisoformat(start_s), date.fromisoformat(end_s)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export trading data to Parquet")
    parser.add_argument("--source", default="redis,sqlite",
                        help="Comma-separated: redis,sqlite")
    parser.add_argument("--date-range", required=True,
                        help="YYYY-MM-DD:YYYY-MM-DD")
    parser.add_argument("--redis-url", default="redis://localhost:6379")
    parser.add_argument("--sqlite-path", default="reporting.db")
    parser.add_argument("--output-dir", default="research/data/parquet")
    parser.add_argument("--with-forward-returns", action="store_true", default=True,
                        help="Compute forward returns on 15m data after export")
    args = parser.parse_args(argv)

    date_range = parse_date_range(args.date_range)
    sources = [s.strip() for s in args.source.split(",")]
    output_dir = Path(args.output_dir)

    from research.data.exporter import DataExporter, build_forward_returns
    import polars as pl

    redis_client = None
    if "redis" in sources:
        import redis
        redis_client = redis.Redis.from_url(args.redis_url)

    sqlite_path = args.sqlite_path if "sqlite" in sources else None

    exporter = DataExporter(
        redis_client=redis_client,
        sqlite_path=sqlite_path,
        output_dir=output_dir,
    )

    if "redis" in sources:
        for stream_key in ["md.features.1m", "md.features.15m", "md.features.1h"]:
            written = exporter.export_redis_stream(stream_key, date_range)
            print(f"  {stream_key}: {len(written)} new files")

    if "sqlite" in sources:
        for table in ["exec_reports", "state_snapshots"]:
            written = exporter.export_sqlite_table(table, date_range)
            print(f"  {table}: {len(written)} new files")

    # Compute forward returns on 15m data
    if args.with_forward_returns:
        features_dir = output_dir / "features_15m"
        parquet_files = sorted(features_dir.glob("*.parquet"))
        if parquet_files:
            df = pl.concat([pl.read_parquet(f) for f in parquet_files])
            df = build_forward_returns(df)
            # Overwrite with forward returns added
            for dt_str, group_df in df.group_by(pl.col("ts").str.slice(0, 10).alias("date")):
                dt_val = dt_str[0] if isinstance(dt_str, tuple) else dt_str
                out_path = features_dir / f"{dt_val}.parquet"
                group_df.write_parquet(out_path)
            print(f"  Forward returns computed for {len(parquet_files)} files")

    print("Export complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script runs with --help**

Run: `python research/scripts/export_data.py --help`
Expected: Shows usage info without errors

- [ ] **Step 3: Commit**

```bash
git add research/scripts/export_data.py
git commit -m "feat(research): add export_data.py CLI script"
```

---

## Chunk 2: Factor Evaluation Layer

### Task 7: Factor registry

**Files:**
- Create: `research/factors/registry.py`
- Create: `tests/test_research/test_registry.py`

- [ ] **Step 1: Write test for factor registry**

```python
# tests/test_research/test_registry.py
def test_registry_lists_all_factors():
    from research.factors.registry import FACTOR_FAMILIES, get_all_factors, get_factors_for_timeframe

    all_factors = get_all_factors()
    assert len(all_factors) > 50  # We have 70+ factors

    # Each factor should appear in exactly one family
    seen = set()
    for family, factors in FACTOR_FAMILIES.items():
        for f_name, _ in factors:
            assert f_name not in seen, f"Duplicate factor: {f_name}"
            seen.add(f_name)


def test_get_factors_for_timeframe():
    from research.factors.registry import get_factors_for_timeframe

    factors_15m = get_factors_for_timeframe("15m")
    assert "ret_15m" in factors_15m
    assert "funding_rate" in factors_15m

    factors_1m = get_factors_for_timeframe("1m")
    assert "book_imbalance_l1" in factors_1m
    assert "bid_slope" in factors_1m
    # 1m-only factors should not be in 15m list unless they also exist at 15m
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_registry.py -v`
Expected: FAIL

- [ ] **Step 3: Implement factor registry**

```python
# research/factors/registry.py
"""Factor registry with family grouping and timeframe annotations."""
from __future__ import annotations

# Each entry is (factor_name, set_of_timeframes)
FACTOR_FAMILIES: dict[str, list[tuple[str, set[str]]]] = {
    "microstructure": [
        ("book_imbalance_l1", {"1m", "15m"}),
        ("book_imbalance_l5", {"1m", "15m"}),
        ("book_imbalance_l10", {"1m", "15m"}),
        ("book_imbalance", {"1m"}),
        ("spread_bps", {"1m", "15m"}),
        ("liquidity_score", {"1m", "15m"}),
        ("top_depth_usd", {"1m", "15m"}),
        ("top_depth_usd_l10", {"15m"}),
    ],
    "microstructure_dynamics": [
        ("microprice_change_1m", {"1m", "15m"}),
        ("book_imbalance_change_1m", {"1m", "15m"}),
        ("spread_change_1m", {"1m", "15m"}),
        ("depth_change_1m", {"1m", "15m"}),
        ("bid_slope", {"1m", "15m"}),
        ("ask_slope", {"1m", "15m"}),
        ("queue_imbalance_l1", {"1m", "15m"}),
    ],
    "order_flow": [
        ("trade_volume_buy_ratio", {"1m", "15m"}),
        ("aggr_delta_1m", {"1m", "15m"}),
        ("aggr_delta_5m", {"1m", "15m"}),
        ("volume_imbalance_1m", {"1m", "15m"}),
        ("volume_imbalance_5m", {"1m", "15m"}),
        ("buy_pressure_1m", {"1m", "15m"}),
        ("sell_pressure_1m", {"1m", "15m"}),
        ("absorption_ratio_bid", {"1m", "15m"}),
        ("absorption_ratio_ask", {"1m", "15m"}),
    ],
    "momentum": [
        ("ret_15m", {"15m"}),
        ("ret_30m", {"15m"}),
        ("ret_1h", {"15m", "1h"}),
        ("ret_4h", {"15m", "1h"}),
        ("ret_24h", {"15m"}),
        ("trend_15m", {"15m"}),
        ("trend_1h", {"15m", "1h"}),
        ("trend_4h", {"15m", "1h"}),
        ("trend_agree", {"15m", "1h"}),
        ("trend_strength_15m", {"15m"}),
        ("rsi_14_1m", {"15m"}),
    ],
    "volatility": [
        ("vol_15m", {"15m"}),
        ("vol_1h", {"15m", "1h"}),
        ("vol_4h", {"15m", "1h"}),
        ("vol_spike", {"15m"}),
        ("vol_regime", {"15m", "1h"}),
        ("vol_15m_p90", {"15m"}),
    ],
    "regime": [
        ("liq_regime", {"15m"}),
        ("liquidity_drop", {"15m"}),
        ("top_depth_usd_p10", {"15m"}),
    ],
    "funding_basis": [
        ("funding_rate", {"15m", "1h"}),
        ("basis_bps", {"15m", "1h"}),
        ("oi_change_15m", {"15m"}),
    ],
    "cross_market": [
        ("btc_ret_1m", {"1m", "15m"}),
        ("btc_ret_15m", {"15m"}),
        ("eth_ret_1m", {"1m", "15m"}),
        ("eth_ret_15m", {"15m"}),
        ("corr_btc_1h", {"15m"}),
        ("corr_eth_1h", {"15m"}),
        ("market_ret_mean_1m", {"1m", "15m"}),
        ("market_ret_std_1m", {"1m", "15m"}),
    ],
    "execution_feedback": [
        ("reject_rate_15m", {"15m"}),
        ("slippage_bps_15m", {"15m"}),
        ("p95_latency_ms_15m", {"15m"}),
    ],
}


def get_all_factors() -> list[str]:
    """Return flat list of all factor names."""
    return [name for factors in FACTOR_FAMILIES.values() for name, _ in factors]


def get_factors_for_timeframe(timeframe: str) -> list[str]:
    """Return factors available at a given timeframe (1m, 15m, 1h)."""
    return [
        name
        for factors in FACTOR_FAMILIES.values()
        for name, tfs in factors
        if timeframe in tfs
    ]


def get_family(factor_name: str) -> str | None:
    """Return the family name for a given factor."""
    for family, factors in FACTOR_FAMILIES.items():
        for name, _ in factors:
            if name == factor_name:
                return family
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/factors/registry.py tests/test_research/test_registry.py
git commit -m "feat(research): add factor registry with family grouping and timeframe annotations"
```

---

### Task 8: Factor evaluator — IC/ICIR computation

**Files:**
- Create: `research/factors/evaluator.py`
- Create: `tests/test_research/test_evaluator.py`

- [ ] **Step 1: Write test for single-factor IC computation**

```python
# tests/test_research/test_evaluator.py
import polars as pl
import numpy as np
import pytest


def test_compute_ic_series():
    """IC should measure rank correlation between factor and forward returns per bar."""
    from research.factors.evaluator import compute_ic_series

    np.random.seed(42)
    n = 200
    # Create factor with known positive correlation to forward returns
    factor_vals = np.random.randn(n)
    ret_vals = factor_vals * 0.5 + np.random.randn(n) * 0.3  # correlated

    df = pl.DataFrame({
        "ts": [f"2026-03-{15 + i // 96:02d}T{(i % 96) // 4 + 10:02d}:{(i % 4) * 15:02d}:00Z" for i in range(n)],
        "symbol": ["BTC"] * (n // 2) + ["ETH"] * (n // 2),
        "test_factor": factor_vals.tolist(),
        "ret_fwd_15m": ret_vals.tolist(),
    })

    ic_series = compute_ic_series(df, factor_col="test_factor", return_col="ret_fwd_15m")

    # IC should be positive (we built a positive correlation)
    assert ic_series.mean() > 0.2
    assert len(ic_series) > 0


def test_compute_factor_metrics():
    """compute_factor_metrics should return IC_mean, IC_std, ICIR."""
    from research.factors.evaluator import compute_factor_metrics

    np.random.seed(42)
    n = 400
    factor_vals = np.random.randn(n)
    ret_vals = factor_vals * 0.3 + np.random.randn(n) * 0.5

    df = pl.DataFrame({
        "ts": [f"2026-03-{15 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00Z" for i in range(n)],
        "symbol": ["BTC"] * (n // 2) + ["ETH"] * (n // 2),
        "test_factor": factor_vals.tolist(),
        "ret_fwd_15m": ret_vals.tolist(),
    })

    metrics = compute_factor_metrics(df, "test_factor", "ret_fwd_15m")

    assert "ic_mean" in metrics
    assert "ic_std" in metrics
    assert "icir" in metrics
    assert metrics["ic_mean"] > 0
    assert metrics["icir"] > 0  # should be positive given our correlation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_evaluator.py -v`
Expected: FAIL

- [ ] **Step 3: Implement IC computation**

```python
# research/factors/evaluator.py
"""Factor evaluation: IC, ICIR, quantile returns, decay analysis."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import polars as pl


def compute_ic_series(
    df: pl.DataFrame,
    factor_col: str,
    return_col: str,
    window: int = 48,
) -> np.ndarray:
    """Compute rolling rank IC between factor and forward return.

    With only 2 symbols (BTC/ETH), cross-sectional IC per timestamp is degenerate
    (always ±1). Instead, compute time-series IC per symbol over rolling windows,
    then average across symbols.

    Args:
        window: Rolling window size in bars (default 48 = 12 hours of 15m bars).
    """
    from scipy.stats import spearmanr

    all_ics = []
    for _, sym_df in df.sort("ts").group_by("symbol"):
        f = sym_df[factor_col].to_numpy().astype(float)
        r = sym_df[return_col].to_numpy().astype(float)

        for i in range(0, len(f) - window + 1, window // 2):  # 50% overlap
            f_win = f[i:i + window]
            r_win = r[i:i + window]
            mask = ~(np.isnan(f_win) | np.isnan(r_win))
            if mask.sum() < 10:
                continue
            corr, _ = spearmanr(f_win[mask], r_win[mask])
            if not np.isnan(corr):
                all_ics.append(corr)

    return np.array(all_ics) if all_ics else np.array([0.0])


def compute_factor_metrics(
    df: pl.DataFrame,
    factor_col: str,
    return_col: str,
) -> dict[str, float]:
    """Compute IC_mean, IC_std, ICIR for a single factor × return horizon."""
    ic_series = compute_ic_series(df, factor_col, return_col)
    if len(ic_series) == 0:
        return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0, "ic_count": 0}

    ic_mean = float(np.mean(ic_series))
    ic_std = float(np.std(ic_series))
    icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

    return {
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "icir": icir,
        "ic_count": len(ic_series),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/factors/evaluator.py tests/test_research/test_evaluator.py
git commit -m "feat(research): add IC/ICIR factor evaluation"
```

---

### Task 9: Factor evaluator — Quantile returns and monotonicity

**Files:**
- Modify: `research/factors/evaluator.py`
- Modify: `tests/test_research/test_evaluator.py`

- [ ] **Step 1: Write test for quantile returns**

```python
# tests/test_research/test_evaluator.py (append)

def test_compute_quantile_returns():
    """Factors with predictive power should show monotonic quantile returns."""
    from research.factors.evaluator import compute_quantile_returns

    np.random.seed(42)
    n = 500
    factor_vals = np.random.randn(n)
    ret_vals = factor_vals * 0.3 + np.random.randn(n) * 0.2

    df = pl.DataFrame({
        "test_factor": factor_vals.tolist(),
        "ret_fwd_15m": ret_vals.tolist(),
    })

    qr = compute_quantile_returns(df, "test_factor", "ret_fwd_15m", n_quantiles=5)

    assert len(qr) == 5
    # With positive correlation, Q5 (highest factor) should have highest return
    assert qr["mean_return"][-1] > qr["mean_return"][0]
    assert "monotonicity" in qr.columns


def test_compute_factor_autocorrelation():
    """Factor autocorrelation measures turnover stability."""
    from research.factors.evaluator import compute_factor_autocorrelation

    # Highly autocorrelated factor (slow-changing)
    vals = np.cumsum(np.random.randn(100) * 0.1).tolist()
    df = pl.DataFrame({
        "ts": [f"T{i}" for i in range(100)],
        "symbol": ["BTC"] * 100,
        "slow_factor": vals,
    })

    autocorr = compute_factor_autocorrelation(df, "slow_factor")
    assert autocorr > 0.8  # Should be highly autocorrelated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_evaluator.py::test_compute_quantile_returns -v`
Expected: FAIL

- [ ] **Step 3: Implement quantile returns and autocorrelation**

```python
# research/factors/evaluator.py (append)

def compute_quantile_returns(
    df: pl.DataFrame,
    factor_col: str,
    return_col: str,
    n_quantiles: int = 5,
) -> pl.DataFrame:
    """Split rows into quantile buckets by factor value, compute mean return per bucket.

    Returns DataFrame with columns: quantile, mean_return, count, monotonicity.
    """
    clean = df.select([factor_col, return_col]).drop_nulls()
    if len(clean) < n_quantiles * 2:
        return pl.DataFrame({"quantile": [], "mean_return": [], "count": [], "monotonicity": []})

    clean = clean.with_columns(
        pl.col(factor_col)
        .rank("ordinal")
        .floordiv(pl.lit(len(clean) // n_quantiles + 1))
        .clip(0, n_quantiles - 1)
        .alias("quantile")
    )

    result = (
        clean.group_by("quantile")
        .agg([
            pl.col(return_col).mean().alias("mean_return"),
            pl.col(return_col).count().alias("count"),
        ])
        .sort("quantile")
    )

    # Compute monotonicity: Spearman correlation of quantile vs mean_return
    from scipy.stats import spearmanr
    if len(result) >= 3:
        mono_corr, _ = spearmanr(
            result["quantile"].to_numpy(),
            result["mean_return"].to_numpy(),
        )
    else:
        mono_corr = 0.0

    result = result.with_columns(pl.lit(mono_corr).alias("monotonicity"))
    return result


def compute_factor_autocorrelation(
    df: pl.DataFrame,
    factor_col: str,
    lag: int = 1,
) -> float:
    """Compute lag-1 autocorrelation of a factor per symbol, then average."""
    autocorrs = []
    for _, sym_df in df.sort("ts").group_by("symbol"):
        vals = sym_df[factor_col].drop_nulls().to_numpy()
        if len(vals) < lag + 2:
            continue
        corr = np.corrcoef(vals[:-lag], vals[lag:])[0, 1]
        if not np.isnan(corr):
            autocorrs.append(corr)
    return float(np.mean(autocorrs)) if autocorrs else 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_research/test_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/factors/evaluator.py tests/test_research/test_evaluator.py
git commit -m "feat(research): add quantile returns and factor autocorrelation analysis"
```

---

### Task 10: Factor evaluator — Full evaluation pipeline

**Files:**
- Modify: `research/factors/evaluator.py`
- Modify: `tests/test_research/test_evaluator.py`

- [ ] **Step 1: Write test for full factor evaluation**

```python
# tests/test_research/test_evaluator.py (append)

def test_evaluate_all_factors():
    """evaluate_all_factors should produce a summary table for all available factors."""
    from research.factors.evaluator import evaluate_all_factors

    np.random.seed(42)
    n = 200
    df = pl.DataFrame({
        "ts": [f"2026-03-{15 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00Z" for i in range(n)],
        "symbol": ["BTC"] * (n // 2) + ["ETH"] * (n // 2),
        "book_imbalance_l5": np.random.randn(n).tolist(),
        "spread_bps": np.random.randn(n).tolist(),
        "ret_fwd_15m": np.random.randn(n).tolist(),
        "ret_fwd_1h": np.random.randn(n).tolist(),
    })

    summary = evaluate_all_factors(
        df,
        factor_cols=["book_imbalance_l5", "spread_bps"],
        return_cols=["ret_fwd_15m", "ret_fwd_1h"],
    )

    assert len(summary) == 4  # 2 factors × 2 horizons
    assert "factor" in summary.columns
    assert "horizon" in summary.columns
    assert "icir" in summary.columns
    assert "ic_mean" in summary.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_evaluator.py::test_evaluate_all_factors -v`
Expected: FAIL

- [ ] **Step 3: Implement evaluate_all_factors**

```python
# research/factors/evaluator.py (append)

def evaluate_all_factors(
    df: pl.DataFrame,
    factor_cols: Sequence[str] | None = None,
    return_cols: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Evaluate all factor × horizon combinations. Returns summary table sorted by |ICIR|."""
    if return_cols is None:
        return_cols = [c for c in df.columns if c.startswith("ret_fwd_")]
    if factor_cols is None:
        from research.factors.registry import get_all_factors
        all_factors = get_all_factors()
        factor_cols = [c for c in df.columns if c in set(all_factors)]

    rows = []
    for factor in factor_cols:
        if factor not in df.columns:
            continue
        for ret_col in return_cols:
            if ret_col not in df.columns:
                continue
            metrics = compute_factor_metrics(df, factor, ret_col)
            autocorr = compute_factor_autocorrelation(df, factor)
            rows.append({
                "factor": factor,
                "horizon": ret_col.replace("ret_fwd_", ""),
                "ic_mean": metrics["ic_mean"],
                "ic_std": metrics["ic_std"],
                "icir": metrics["icir"],
                "ic_count": metrics["ic_count"],
                "autocorrelation": autocorr,
            })

    if not rows:
        return pl.DataFrame()

    result = pl.DataFrame(rows)
    return result.sort(pl.col("icir").abs(), descending=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/factors/evaluator.py tests/test_research/test_evaluator.py
git commit -m "feat(research): add full factor evaluation pipeline"
```

---

### Task 11: Factor correlation matrix

**Files:**
- Create: `research/factors/correlation.py`
- Create: `tests/test_research/test_correlation.py`

- [ ] **Step 1: Write test for correlation matrix**

```python
# tests/test_research/test_correlation.py
import polars as pl
import numpy as np


def test_compute_correlation_matrix():
    from research.factors.correlation import compute_correlation_matrix

    np.random.seed(42)
    n = 100
    base = np.random.randn(n)

    df = pl.DataFrame({
        "factor_a": base.tolist(),
        "factor_b": (base + np.random.randn(n) * 0.1).tolist(),  # highly correlated
        "factor_c": np.random.randn(n).tolist(),  # uncorrelated
    })

    corr_matrix = compute_correlation_matrix(df, ["factor_a", "factor_b", "factor_c"])

    assert corr_matrix.shape == (3, 3)
    # factor_a and factor_b should be highly correlated
    assert abs(corr_matrix[0, 1]) > 0.9
    # factor_a and factor_c should have low correlation
    assert abs(corr_matrix[0, 2]) < 0.3


def test_find_redundant_factors():
    from research.factors.correlation import find_redundant_factors

    np.random.seed(42)
    n = 100
    base = np.random.randn(n)

    df = pl.DataFrame({
        "factor_a": base.tolist(),
        "factor_b": (base + np.random.randn(n) * 0.05).tolist(),
        "factor_c": np.random.randn(n).tolist(),
    })

    # Give factor_a higher ICIR than factor_b
    icir_map = {"factor_a": 1.5, "factor_b": 0.8, "factor_c": 1.0}
    redundant = find_redundant_factors(df, ["factor_a", "factor_b", "factor_c"], icir_map, threshold=0.8)

    # factor_b should be flagged as redundant (high corr with factor_a, lower ICIR)
    assert "factor_b" in redundant
    assert "factor_a" not in redundant
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_correlation.py -v`
Expected: FAIL

- [ ] **Step 3: Implement correlation analysis**

```python
# research/factors/correlation.py
"""Factor correlation analysis and redundancy detection."""
from __future__ import annotations

import numpy as np
import polars as pl


def compute_correlation_matrix(
    df: pl.DataFrame,
    factor_cols: list[str],
) -> np.ndarray:
    """Compute pairwise Pearson correlation matrix for given factors."""
    data = df.select(factor_cols).to_numpy()
    # Handle NaN
    valid_mask = ~np.isnan(data).any(axis=1)
    data = data[valid_mask]
    if len(data) < 3:
        return np.eye(len(factor_cols))
    return np.corrcoef(data, rowvar=False)


def find_redundant_factors(
    df: pl.DataFrame,
    factor_cols: list[str],
    icir_map: dict[str, float],
    threshold: float = 0.8,
) -> set[str]:
    """Find factors that are redundant (|corr| > threshold with a higher-ICIR factor).

    Returns set of factor names to consider dropping.
    """
    corr_matrix = compute_correlation_matrix(df, factor_cols)
    redundant = set()

    for i in range(len(factor_cols)):
        for j in range(i + 1, len(factor_cols)):
            if abs(corr_matrix[i, j]) > threshold:
                fi, fj = factor_cols[i], factor_cols[j]
                icir_i = abs(icir_map.get(fi, 0))
                icir_j = abs(icir_map.get(fj, 0))
                # Keep the one with higher ICIR
                if icir_i >= icir_j:
                    redundant.add(fj)
                else:
                    redundant.add(fi)

    return redundant
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_correlation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/factors/correlation.py tests/test_research/test_correlation.py
git commit -m "feat(research): add factor correlation matrix and redundancy detection"
```

---

### Task 12: Factor report generation and eval_factors CLI

**Files:**
- Create: `research/factors/report.py`
- Create: `research/scripts/eval_factors.py`

- [ ] **Step 1: Implement report generation**

```python
# research/factors/report.py
"""Generate factor evaluation reports: summary CSV, decay curves, correlation heatmap."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import polars as pl
import numpy as np


def save_summary_csv(summary_df: pl.DataFrame, output_dir: Path) -> Path:
    """Save factor evaluation summary sorted by |ICIR|."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "summary.csv"
    summary_df.write_csv(path)
    return path


def save_ic_decay_plot(
    summary_df: pl.DataFrame,
    output_dir: Path,
    top_n: int = 15,
) -> Path:
    """Plot IC decay curves for top factors across horizons."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)

    # Get top factors by max |ICIR| across horizons
    top_factors = (
        summary_df.group_by("factor")
        .agg(pl.col("icir").abs().max().alias("max_icir"))
        .sort("max_icir", descending=True)
        .head(top_n)["factor"]
        .to_list()
    )

    horizon_order = ["15m", "30m", "1h", "2h", "4h"]

    fig, ax = plt.subplots(figsize=(10, 6))
    for factor in top_factors:
        factor_data = summary_df.filter(pl.col("factor") == factor)
        horizons = []
        ic_means = []
        for h in horizon_order:
            row = factor_data.filter(pl.col("horizon") == h)
            if len(row) > 0:
                horizons.append(h)
                ic_means.append(row["ic_mean"][0])
        if horizons:
            ax.plot(horizons, ic_means, marker="o", label=factor)

    ax.set_xlabel("Forward Return Horizon")
    ax.set_ylabel("IC Mean")
    ax.set_title("IC Decay Curves (Top Factors)")
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=7)
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color="black", linestyle="--", alpha=0.3)
    plt.tight_layout()

    path = output_dir / "ic_decay_curves.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def save_correlation_heatmap(
    corr_matrix: np.ndarray,
    factor_names: list[str],
    output_dir: Path,
) -> Path:
    """Save factor correlation heatmap."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(max(8, len(factor_names) * 0.5),
                                     max(6, len(factor_names) * 0.4)))
    im = ax.imshow(corr_matrix, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(factor_names)))
    ax.set_yticks(range(len(factor_names)))
    ax.set_xticklabels(factor_names, rotation=45, ha="right", fontsize=7)
    ax.set_yticklabels(factor_names, fontsize=7)
    ax.set_title("Factor Correlation Matrix")
    plt.colorbar(im, ax=ax)
    plt.tight_layout()

    path = output_dir / "correlation_matrix.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
```

- [ ] **Step 2: Implement eval_factors CLI**

```python
# research/scripts/eval_factors.py
"""CLI: Evaluate factor effectiveness using IC/ICIR analysis."""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate factor IC/ICIR")
    parser.add_argument("--data-dir", default="research/data/parquet",
                        help="Directory with Parquet files")
    parser.add_argument("--timeframe", default="15m", choices=["1m", "15m", "1h"],
                        help="Which feature timeframe to evaluate")
    parser.add_argument("--output", default="research/reports/factor_eval",
                        help="Output directory for reports")
    parser.add_argument("--summary", action="store_true",
                        help="Print summary table to stdout")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output)
    feature_dir = data_dir / f"features_{args.timeframe}"

    parquet_files = sorted(feature_dir.glob("*.parquet"))
    if not parquet_files:
        print(f"No Parquet files found in {feature_dir}")
        return

    print(f"Loading {len(parquet_files)} files from {feature_dir}...")
    df = pl.concat([pl.read_parquet(f) for f in parquet_files])
    print(f"  {len(df)} rows, {len(df.columns)} columns")

    from research.factors.registry import get_factors_for_timeframe
    from research.factors.evaluator import evaluate_all_factors
    from research.factors.correlation import compute_correlation_matrix, find_redundant_factors
    from research.factors.report import save_summary_csv, save_ic_decay_plot, save_correlation_heatmap

    factor_cols = [f for f in get_factors_for_timeframe(args.timeframe) if f in df.columns]
    return_cols = [c for c in df.columns if c.startswith("ret_fwd_")]

    if not factor_cols:
        print("No matching factor columns found in data")
        return
    if not return_cols:
        print("No forward return columns found. Run export with --with-forward-returns first.")
        return

    print(f"Evaluating {len(factor_cols)} factors × {len(return_cols)} horizons...")
    summary = evaluate_all_factors(df, factor_cols, return_cols)

    # Save reports
    save_summary_csv(summary, output_dir)
    save_ic_decay_plot(summary, output_dir)

    # Correlation analysis
    corr_matrix = compute_correlation_matrix(df, factor_cols)
    save_correlation_heatmap(corr_matrix, factor_cols, output_dir)

    # Find redundant factors
    icir_map = {}
    for row in summary.iter_rows(named=True):
        key = row["factor"]
        if key not in icir_map or abs(row["icir"]) > abs(icir_map[key]):
            icir_map[key] = row["icir"]

    redundant = find_redundant_factors(df, factor_cols, icir_map)

    print(f"\nResults saved to {output_dir}/")
    print(f"  Effective factors (|ICIR| > 0.5): "
          f"{len(summary.filter(pl.col('icir').abs() > 0.5).unique('factor'))}")
    print(f"  Noise factors (|ICIR| < 0.2): "
          f"{len(summary.filter(pl.col('icir').abs() < 0.2).unique('factor'))}")
    print(f"  Redundant factors: {redundant if redundant else 'none'}")

    if args.summary:
        print("\nTop factors by |ICIR|:")
        print(summary.head(20))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify CLI help runs**

Run: `python research/scripts/eval_factors.py --help`
Expected: Shows usage without error

- [ ] **Step 4: Commit**

```bash
git add research/factors/report.py research/scripts/eval_factors.py
git commit -m "feat(research): add factor report generation and eval_factors CLI"
```

---

## Chunk 3: Backtest Engine

### Task 13: Cost model

**Files:**
- Create: `research/backtest/cost_model.py`
- Create: `tests/test_research/test_cost_model.py`

- [ ] **Step 1: Write test for cost model**

```python
# tests/test_research/test_cost_model.py
from dataclasses import dataclass
import pytest


@dataclass
class FakeBar:
    top_depth_usd: float = 500000.0
    funding_rate: float = 0.0001


def test_trade_cost_includes_fee_and_slippage():
    from research.backtest.cost_model import CryptoPerpCostModel

    model = CryptoPerpCostModel(taker_fee_bps=3.5, slippage_base_bps=1.0)
    bar = FakeBar(top_depth_usd=500000.0)

    cost = model.trade_cost(notional=1000.0, bar=bar)

    # fee = 1000 * 3.5/10000 = 0.35
    # slippage = 1000 * (1.0 + 1000/500000 * 100) / 10000 = 1000 * 1.2 / 10000 = 0.12
    assert cost > 0.35  # at least the fee
    assert cost < 1.0   # reasonable


def test_funding_cost_scales_with_position():
    from research.backtest.cost_model import CryptoPerpCostModel

    model = CryptoPerpCostModel()
    bar = FakeBar(funding_rate=0.0001)

    # 15m bar = 1/32 of 8h funding period
    cost = model.funding_cost(position_notional=10000.0, bar=bar)
    expected = 10000.0 * 0.0001 / 32
    assert abs(cost - expected) < 1e-6


def test_zero_depth_uses_max_slippage():
    from research.backtest.cost_model import CryptoPerpCostModel

    model = CryptoPerpCostModel()
    bar = FakeBar(top_depth_usd=0.0)

    cost = model.trade_cost(notional=1000.0, bar=bar)
    assert cost > 0  # Should not crash, uses max slippage cap
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_cost_model.py -v`
Expected: FAIL

- [ ] **Step 3: Implement cost model**

```python
# research/backtest/cost_model.py
"""Trading cost model for Hyperliquid perpetual futures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CryptoPerpCostModel:
    """Depth-aware cost model with continuous funding charges."""

    taker_fee_bps: float = 3.5
    maker_fee_bps: float = 1.0
    slippage_base_bps: float = 1.0
    slippage_impact_coeff: float = 100.0  # notional/depth multiplier
    max_slippage_bps: float = 20.0
    funding_interval_hours: float = 8.0

    @property
    def _bars_per_funding(self) -> float:
        """Number of 15m bars per funding interval."""
        return self.funding_interval_hours * 4  # 4 bars per hour

    def trade_cost(self, notional: float, bar: Any) -> float:
        """Total cost for executing a trade: fee + slippage."""
        fee = abs(notional) * self.taker_fee_bps / 10000

        depth = getattr(bar, "top_depth_usd", 0)
        if depth > 0:
            impact_bps = self.slippage_base_bps + abs(notional) / depth * self.slippage_impact_coeff
        else:
            impact_bps = self.max_slippage_bps
        impact_bps = min(impact_bps, self.max_slippage_bps)
        slippage = abs(notional) * impact_bps / 10000

        return fee + slippage

    def funding_cost(self, position_notional: float, bar: Any) -> float:
        """Funding cost for holding a position through one 15m bar."""
        rate = getattr(bar, "funding_rate", 0)
        return abs(position_notional) * abs(rate) / self._bars_per_funding
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_cost_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/backtest/cost_model.py tests/test_research/test_cost_model.py
git commit -m "feat(research): add depth-aware crypto perp cost model"
```

---

### Task 14: Scorer — Comprehensive metrics

**Files:**
- Create: `research/backtest/scorer.py`
- Create: `tests/test_research/test_scorer.py`

- [ ] **Step 1: Write test for scorer**

```python
# tests/test_research/test_scorer.py
import numpy as np
import polars as pl
import pytest


def test_compute_sharpe():
    from research.backtest.scorer import compute_sharpe

    # Returns: 1% per bar, no volatility → high sharpe
    returns = np.ones(100) * 0.01
    sharpe = compute_sharpe(returns, bars_per_day=96)
    assert sharpe > 5.0  # Very high sharpe with no vol


def test_compute_max_drawdown():
    from research.backtest.scorer import compute_max_drawdown

    equity = [100, 105, 103, 108, 95, 110, 100]
    dd = compute_max_drawdown(equity)
    # Max DD: from 108 to 95 = (108-95)/108 ≈ 0.1204
    assert abs(dd - (108 - 95) / 108) < 0.01


def test_scorer_produces_0_to_100():
    from research.backtest.scorer import Scorer

    equity_curve = np.cumsum(np.random.randn(500) * 0.001) + 100
    equity_curve = equity_curve.tolist()
    trades = pl.DataFrame({
        "pnl": np.random.randn(50).tolist(),
    })

    scorer = Scorer()
    result = scorer.score(equity_curve, trades)

    assert "composite_score" in result
    assert 0 <= result["composite_score"] <= 100
    assert "sharpe" in result
    assert "max_drawdown" in result
    assert "win_rate" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_scorer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement scorer**

```python
# research/backtest/scorer.py
"""Comprehensive strategy scoring with weighted composite metrics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import polars as pl


def compute_sharpe(returns: np.ndarray | Sequence[float], bars_per_day: int = 96, risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio. 96 bars/day = 15-min bars × 24h."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2 or np.std(r) < 1e-12:
        return 0.0
    excess = r - risk_free / (bars_per_day * 365)
    return float(np.mean(excess) / np.std(excess) * np.sqrt(bars_per_day * 365))


def compute_sortino(returns: np.ndarray | Sequence[float], bars_per_day: int = 96) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) < 1 or np.std(downside) < 1e-12:
        return 0.0 if np.mean(r) <= 0 else 10.0  # cap
    return float(np.mean(r) / np.std(downside) * np.sqrt(bars_per_day * 365))


def compute_max_drawdown(equity: Sequence[float]) -> float:
    """Maximum drawdown as a fraction (0 to 1)."""
    eq = np.asarray(equity, dtype=float)
    if len(eq) < 2:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.where(peak > 0, peak, 1.0)
    return float(np.max(dd))


def compute_calmar(returns: np.ndarray | Sequence[float], equity: Sequence[float], bars_per_day: int = 96) -> float:
    """Calmar ratio: annualized return / max drawdown."""
    r = np.asarray(returns, dtype=float)
    ann_ret = float(np.mean(r) * bars_per_day * 365)
    mdd = compute_max_drawdown(equity)
    if mdd < 1e-10:
        return 0.0 if ann_ret <= 0 else 10.0
    return min(float(ann_ret / mdd), 10.0)


@dataclass
class Scorer:
    """Weighted composite scoring system for strategy evaluation."""

    weights: dict[str, float] = field(default_factory=lambda: {
        "sharpe": 0.25,
        "sortino": 0.15,
        "calmar": 0.10,
        "win_rate": 0.15,
        "profit_factor": 0.15,
        "net_pnl": 0.10,
        "max_drawdown": 0.10,
    })

    def score(self, equity_curve: Sequence[float], trades: pl.DataFrame) -> dict[str, float]:
        """Compute all metrics and weighted composite score (0-100)."""
        eq = np.asarray(equity_curve, dtype=float)
        returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([])

        # Individual metrics
        sharpe = compute_sharpe(returns)
        sortino = compute_sortino(returns)
        mdd = compute_max_drawdown(equity_curve)
        calmar = compute_calmar(returns, equity_curve)

        # Trade-level metrics
        pnl_col = trades["pnl"].to_numpy() if "pnl" in trades.columns else np.array([])
        win_rate = float(np.mean(pnl_col > 0)) if len(pnl_col) > 0 else 0.0

        gross_profit = float(np.sum(pnl_col[pnl_col > 0])) if len(pnl_col) > 0 else 0.0
        gross_loss = abs(float(np.sum(pnl_col[pnl_col < 0]))) if len(pnl_col) > 0 else 1.0
        profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0

        net_pnl = float(eq[-1] - eq[0]) / eq[0] if len(eq) > 1 and eq[0] > 0 else 0.0

        # Normalize to 0-100
        normalized = {
            "sharpe": np.clip(sharpe / 3.0 * 100, 0, 100),        # 3.0 → 100
            "sortino": np.clip(sortino / 4.0 * 100, 0, 100),      # 4.0 → 100
            "calmar": np.clip(calmar / 5.0 * 100, 0, 100),        # 5.0 → 100
            "win_rate": win_rate * 100,                             # 1.0 → 100
            "profit_factor": np.clip(profit_factor / 3.0 * 100, 0, 100),  # 3.0 → 100
            "net_pnl": np.clip((net_pnl + 0.5) / 1.0 * 100, 0, 100),     # +50% → 100
            "max_drawdown": np.clip((1 - mdd / 0.5) * 100, 0, 100),       # 0%→100, 50%→0
        }

        composite = sum(normalized[k] * self.weights[k] for k in self.weights)

        return {
            "composite_score": float(composite),
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "max_drawdown": mdd,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "net_pnl_pct": net_pnl,
            "total_trades": len(pnl_col),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_scorer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/backtest/scorer.py tests/test_research/test_scorer.py
git commit -m "feat(research): add comprehensive strategy scorer with weighted composite"
```

---

### Task 15: Backtest engine core

**Files:**
- Create: `research/backtest/engine.py`
- Create: `tests/test_research/test_engine.py`

- [ ] **Step 1: Write test for backtest engine**

```python
# tests/test_research/test_engine.py
import polars as pl
import numpy as np
import pytest


def test_backtest_engine_basic():
    """Engine should run a simple strategy and produce equity curve + trades."""
    from research.backtest.engine import BacktestEngine, BarData
    from research.backtest.cost_model import CryptoPerpCostModel

    # Create simple price data: BTC goes up
    bars = []
    for i in range(20):
        bars.append(BarData(
            ts=f"2026-03-15T{10 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
            symbol="BTC",
            mid_px=100.0 + i * 0.5,
            top_depth_usd=500000.0,
            funding_rate=0.0001,
            features={},
        ))

    class AlwaysLong:
        def on_bar(self, bar):
            return {"BTC": 0.5}  # 50% long BTC

    engine = BacktestEngine(cost_model=CryptoPerpCostModel())
    result = engine.run(
        strategy=AlwaysLong(),
        bars=bars,
        initial_capital=10000.0,
    )

    assert len(result.equity_curve) == 20
    assert result.equity_curve[-1] > result.equity_curve[0]  # Should profit
    assert len(result.trades) > 0  # At least the initial entry


def test_backtest_engine_costs_reduce_pnl():
    """With high fees, the strategy should perform worse."""
    from research.backtest.engine import BacktestEngine, BarData
    from research.backtest.cost_model import CryptoPerpCostModel

    bars = []
    for i in range(20):
        bars.append(BarData(
            ts=f"T{i}",
            symbol="BTC",
            mid_px=100.0,  # flat price
            top_depth_usd=500000.0,
            funding_rate=0.0001,
            features={},
        ))

    class Flipper:
        """Flip position every bar to generate maximum trading costs."""
        def __init__(self):
            self.long = True
        def on_bar(self, bar):
            self.long = not self.long
            return {"BTC": 0.5 if self.long else -0.5}

    engine = BacktestEngine(cost_model=CryptoPerpCostModel(taker_fee_bps=10.0))
    result = engine.run(strategy=Flipper(), bars=bars, initial_capital=10000.0)

    # Flat price + high fees + high turnover → loss
    assert result.equity_curve[-1] < 10000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_engine.py -v`
Expected: FAIL

- [ ] **Step 3: Implement backtest engine**

```python
# research/backtest/engine.py
"""Event-driven backtesting engine for crypto perpetual futures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

import polars as pl

from research.backtest.cost_model import CryptoPerpCostModel


@dataclass
class BarData:
    ts: str
    symbol: str
    mid_px: float
    top_depth_usd: float = 500000.0
    funding_rate: float = 0.0
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestResult:
    equity_curve: list[float]
    trades: pl.DataFrame
    metrics: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    def on_bar(self, bar: BarData) -> dict[str, float]:
        """Return target weights per symbol. E.g. {'BTC': 0.15, 'ETH': -0.05}"""
        ...


class BacktestEngine:
    """Simple event-driven backtest: iterates bars, diffs positions, applies costs."""

    def __init__(self, cost_model: CryptoPerpCostModel | None = None):
        self.cost_model = cost_model or CryptoPerpCostModel()

    def run(
        self,
        strategy: Strategy,
        bars: Sequence[BarData],
        initial_capital: float = 10000.0,
    ) -> BacktestResult:
        equity = initial_capital
        # positions stored as (qty_in_units, entry_price)
        pos_qty: dict[str, float] = {}    # symbol → qty in asset units
        prev_prices: dict[str, float] = {}  # symbol → last known price
        equity_curve: list[float] = []
        trade_records: list[dict[str, Any]] = []

        for bar in bars:
            # 1. Mark-to-market BEFORE updating price
            if bar.symbol in pos_qty and bar.symbol in prev_prices:
                qty = pos_qty[bar.symbol]
                price_change = bar.mid_px - prev_prices[bar.symbol]
                pnl = qty * price_change  # qty can be negative (short)
                equity += pnl

            # 2. Update price
            prev_prices[bar.symbol] = bar.mid_px

            # 3. Get target weights from strategy
            target_weights = strategy.on_bar(bar)

            # 4. Convert weights to target qty and execute trades
            for symbol, weight in target_weights.items():
                px = prev_prices.get(symbol, bar.mid_px)
                if px <= 0:
                    continue
                target_notional = equity * weight
                target_qty = target_notional / px
                current_qty = pos_qty.get(symbol, 0.0)
                trade_qty = target_qty - current_qty
                trade_notional = abs(trade_qty * px)

                if trade_notional < 1.0:  # skip tiny trades
                    continue

                # Apply trade cost
                cost = self.cost_model.trade_cost(trade_notional, bar)
                equity -= cost

                trade_records.append({
                    "ts": bar.ts,
                    "symbol": symbol,
                    "side": "BUY" if trade_qty > 0 else "SELL",
                    "notional": trade_notional,
                    "price": px,
                    "cost": cost,
                    "pnl": 0.0,
                })

                pos_qty[symbol] = target_qty

            # 5. Apply funding cost on all held positions
            for symbol, qty in pos_qty.items():
                notional = abs(qty * prev_prices.get(symbol, 0))
                if notional > 0:
                    funding = self.cost_model.funding_cost(notional, bar)
                    equity -= funding

            equity_curve.append(equity)

        # Compute trade PnL (simplified: assign proportional PnL)
        trades_df = pl.DataFrame(trade_records) if trade_records else pl.DataFrame({
            "ts": [], "symbol": [], "side": [], "notional": [],
            "price": [], "cost": [], "pnl": [],
        })

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades_df,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/backtest/engine.py tests/test_research/test_engine.py
git commit -m "feat(research): add event-driven backtest engine with cost model"
```

---

### Task 16: Built-in strategies (SingleFactor, WeightedFactor)

**Files:**
- Create: `research/backtest/strategies.py`
- Create: `tests/test_research/test_strategies.py`

- [ ] **Step 1: Write test for SingleFactorStrategy**

```python
# tests/test_research/test_strategies.py
from research.backtest.engine import BarData


def test_single_factor_strategy_goes_long_on_positive():
    from research.backtest.strategies import SingleFactorStrategy

    strategy = SingleFactorStrategy(
        factor_name="book_imbalance_l5",
        long_threshold=0.5,
        short_threshold=-0.5,
        max_weight=0.3,
    )

    bar = BarData(ts="T1", symbol="BTC", mid_px=100.0,
                  features={"book_imbalance_l5": 0.8})
    weights = strategy.on_bar(bar)
    assert weights["BTC"] > 0  # Should go long


def test_single_factor_strategy_goes_short_on_negative():
    from research.backtest.strategies import SingleFactorStrategy

    strategy = SingleFactorStrategy(
        factor_name="book_imbalance_l5",
        long_threshold=0.5,
        short_threshold=-0.5,
        max_weight=0.3,
    )

    bar = BarData(ts="T1", symbol="BTC", mid_px=100.0,
                  features={"book_imbalance_l5": -0.8})
    weights = strategy.on_bar(bar)
    assert weights["BTC"] < 0  # Should go short


def test_weighted_factor_strategy():
    from research.backtest.strategies import WeightedFactorStrategy

    strategy = WeightedFactorStrategy(
        factor_weights={"book_imbalance_l5": 0.6, "aggr_delta_5m": 0.4},
        long_threshold=0.3,
        short_threshold=-0.3,
        max_weight=0.2,
    )

    bar = BarData(ts="T1", symbol="BTC", mid_px=100.0,
                  features={"book_imbalance_l5": 0.8, "aggr_delta_5m": 0.5})
    weights = strategy.on_bar(bar)
    assert weights["BTC"] > 0  # Both factors positive → long
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_strategies.py -v`
Expected: FAIL

- [ ] **Step 3: Implement strategies**

```python
# research/backtest/strategies.py
"""Built-in backtest strategies for factor evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field

from research.backtest.engine import BarData


@dataclass
class SingleFactorStrategy:
    """Go long/short based on a single factor value exceeding thresholds."""

    factor_name: str
    long_threshold: float = 0.5
    short_threshold: float = -0.5
    max_weight: float = 0.3

    def on_bar(self, bar: BarData) -> dict[str, float]:
        val = bar.features.get(self.factor_name, 0.0)
        if val > self.long_threshold:
            weight = self.max_weight
        elif val < self.short_threshold:
            weight = -self.max_weight
        else:
            weight = 0.0
        return {bar.symbol: weight}


@dataclass
class WeightedFactorStrategy:
    """Weighted combination of multiple factors to generate signal."""

    factor_weights: dict[str, float]  # factor_name → weight (should sum to 1)
    long_threshold: float = 0.3
    short_threshold: float = -0.3
    max_weight: float = 0.2

    def on_bar(self, bar: BarData) -> dict[str, float]:
        signal = 0.0
        total_weight = 0.0
        for factor, w in self.factor_weights.items():
            val = bar.features.get(factor)
            if val is not None:
                signal += val * w
                total_weight += abs(w)

        if total_weight > 0:
            signal /= total_weight  # normalize

        if signal > self.long_threshold:
            weight = self.max_weight
        elif signal < self.short_threshold:
            weight = -self.max_weight
        else:
            weight = 0.0

        return {bar.symbol: weight}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_strategies.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/backtest/strategies.py tests/test_research/test_strategies.py
git commit -m "feat(research): add SingleFactor and WeightedFactor strategies"
```

---

### Task 17: RuleBasedStrategy — production baseline replication

**Files:**
- Create: `research/backtest/rule_based_strategy.py`
- Create: `tests/test_research/test_rule_based_strategy.py`

**Context:**
- Must replicate the stateful constraints from production Layer2 (`services/ai_decision/app.py`)
- This is the baseline for comparing all other strategies against
- Key rules: profit target (25bps), stop loss (40bps), min trade interval (90min), max trades/day (15), direction reversal penalty, position max age (30min), bearish regime long block, PnL-based scaling

- [ ] **Step 1: Write test for profit target and stop loss**

```python
# tests/test_research/test_rule_based_strategy.py
from research.backtest.engine import BarData


def test_profit_target_closes_position():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy()
    # Open a long position
    bar1 = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    weights1 = strategy.on_bar(bar1)

    # Price up 30bps → should trigger profit target (25bps)
    bar2 = BarData(ts="2026-03-15T10:15:00Z", symbol="BTC", mid_px=100.03,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    weights2 = strategy.on_bar(bar2)
    # Should close or reduce position
    assert abs(weights2.get("BTC", 0)) < abs(weights1.get("BTC", 0))


def test_stop_loss_closes_position():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy()
    bar1 = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    strategy.on_bar(bar1)

    # Price down 50bps → should trigger stop loss (40bps)
    bar2 = BarData(ts="2026-03-15T10:15:00Z", symbol="BTC", mid_px=99.95,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": -0.005})
    weights2 = strategy.on_bar(bar2)
    assert weights2.get("BTC", 0) == 0.0  # Should be flat


def test_min_trade_interval():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy(min_trade_interval_min=90)
    bar1 = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                   features={"trend_agree": 1, "trend_strength_15m": 1.5, "ret_1h": 0.005})
    strategy.on_bar(bar1)

    # Only 15 min later, should not trade
    bar2 = BarData(ts="2026-03-15T10:15:00Z", symbol="BTC", mid_px=100.5,
                   features={"trend_agree": -1, "trend_strength_15m": 1.5, "ret_1h": -0.005})
    weights2 = strategy.on_bar(bar2)
    # Should hold previous position, not reverse


def test_bearish_regime_blocks_longs():
    from research.backtest.rule_based_strategy import RuleBasedStrategy

    strategy = RuleBasedStrategy(bearish_regime_long_block=True)
    bar = BarData(ts="2026-03-15T10:00:00Z", symbol="BTC", mid_px=100.0,
                  features={"trend_agree": 1, "ret_1h": -0.008, "trend_1h": -1,
                            "trend_strength_15m": 1.5})
    weights = strategy.on_bar(bar)
    # Bearish regime (ret_1h < -0.5%) → no new longs
    assert weights.get("BTC", 0) <= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_rule_based_strategy.py -v`
Expected: FAIL

- [ ] **Step 3: Implement RuleBasedStrategy**

```python
# research/backtest/rule_based_strategy.py
"""RuleBasedStrategy: replicates production Layer2 decision logic for baseline comparison."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from research.backtest.engine import BarData


def _parse_ts(ts: str) -> datetime:
    """Parse ISO timestamp to datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass
class PositionState:
    symbol: str
    entry_px: float
    entry_ts: str
    weight: float


@dataclass
class RuleBasedStrategy:
    """Replicate production Layer2 stateful constraints.

    Parameters match config/trading_params.json V9 defaults.
    """
    # Position management
    profit_target_bps: float = 25.0
    stop_loss_bps: float = 40.0
    position_max_age_min: int = 30
    # Trade gating
    min_trade_interval_min: int = 90
    max_trades_per_day: int = 15
    # Regime
    bearish_regime_long_block: bool = True
    bearish_ret_1h_threshold: float = -0.005
    # Exposure
    max_weight: float = 0.30
    min_confidence: float = 0.60
    # Signal
    trending_strength_threshold: float = 1.0

    # Internal state
    _positions: dict[str, PositionState] = field(default_factory=dict)
    _last_trade_ts: dict[str, str] = field(default_factory=dict)
    _daily_trade_count: int = 0
    _current_day: str = ""

    def on_bar(self, bar: BarData) -> dict[str, float]:
        features = bar.features
        day = bar.ts[:10]

        # Reset daily trade count
        if day != self._current_day:
            self._current_day = day
            self._daily_trade_count = 0

        # Check existing position for profit target / stop loss / max age
        if bar.symbol in self._positions:
            pos = self._positions[bar.symbol]
            pnl_bps = (bar.mid_px - pos.entry_px) / pos.entry_px * 10000
            age_min = (_parse_ts(bar.ts) - _parse_ts(pos.entry_ts)).total_seconds() / 60

            # Profit target
            if pnl_bps >= self.profit_target_bps:
                del self._positions[bar.symbol]
                return {bar.symbol: 0.0}

            # Stop loss
            if pnl_bps <= -self.stop_loss_bps:
                del self._positions[bar.symbol]
                return {bar.symbol: 0.0}

            # Position max age (close if not profitable)
            if age_min >= self.position_max_age_min and pnl_bps <= 0:
                del self._positions[bar.symbol]
                return {bar.symbol: 0.0}

        # Check trade gating
        if self._daily_trade_count >= self.max_trades_per_day:
            return {bar.symbol: self._positions.get(bar.symbol, PositionState(bar.symbol, 0, bar.ts, 0)).weight}

        if bar.symbol in self._last_trade_ts:
            elapsed = (_parse_ts(bar.ts) - _parse_ts(self._last_trade_ts[bar.symbol])).total_seconds() / 60
            if elapsed < self.min_trade_interval_min:
                return {bar.symbol: self._positions.get(bar.symbol, PositionState(bar.symbol, 0, bar.ts, 0)).weight}

        # Determine direction from features
        trend_agree = features.get("trend_agree", 0)
        trend_strength = features.get("trend_strength_15m", 0)
        ret_1h = features.get("ret_1h", 0)

        # Bearish regime check
        is_bearish = ret_1h < self.bearish_ret_1h_threshold and features.get("trend_1h", 0) < 0

        # Generate signal
        weight = 0.0
        if trend_agree != 0 and abs(trend_strength) >= self.trending_strength_threshold:
            if trend_agree > 0:
                if is_bearish and self.bearish_regime_long_block:
                    weight = 0.0  # Block longs in bearish regime
                else:
                    weight = self.max_weight
            else:
                weight = -self.max_weight

        # Record trade if weight changed
        current_weight = self._positions.get(bar.symbol, PositionState(bar.symbol, 0, bar.ts, 0)).weight
        if abs(weight - current_weight) > 0.01:
            self._daily_trade_count += 1
            self._last_trade_ts[bar.symbol] = bar.ts
            if abs(weight) > 0.01:
                self._positions[bar.symbol] = PositionState(
                    symbol=bar.symbol, entry_px=bar.mid_px,
                    entry_ts=bar.ts, weight=weight,
                )
            elif bar.symbol in self._positions:
                del self._positions[bar.symbol]

        return {bar.symbol: weight}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_rule_based_strategy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/backtest/rule_based_strategy.py tests/test_research/test_rule_based_strategy.py
git commit -m "feat(research): add RuleBasedStrategy replicating production Layer2 constraints"
```

---

### Task 18: Walk-Forward validation

**Files:**
- Create: `research/backtest/walk_forward.py`
- Create: `tests/test_research/test_walk_forward.py`

**Context:**
- Walk-forward prevents overfitting by testing on out-of-sample windows
- Scheme: train=7d, test=2d, step=2d (rolling)
- Final metrics = weighted average across all test windows

- [ ] **Step 1: Write test for walk-forward**

```python
# tests/test_research/test_walk_forward.py
import pytest


def test_generate_walk_forward_windows():
    from research.backtest.walk_forward import generate_windows

    # 20 days of data
    timestamps = [f"2026-03-{d:02d}T10:00:00Z" for d in range(1, 21)]
    windows = generate_windows(timestamps, train_days=7, test_days=2, step_days=2)

    assert len(windows) > 0
    for train_range, test_range in windows:
        # Train period should be before test period
        assert train_range[1] <= test_range[0]


def test_walk_forward_runner():
    from research.backtest.walk_forward import WalkForwardRunner
    from research.backtest.engine import BacktestEngine, BarData
    from research.backtest.cost_model import CryptoPerpCostModel
    from research.backtest.strategies import SingleFactorStrategy
    import numpy as np

    np.random.seed(42)
    bars = []
    for i in range(500):
        bars.append(BarData(
            ts=f"2026-03-{1 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00Z",
            symbol="BTC",
            mid_px=100.0 + np.cumsum(np.random.randn(1))[0] * 0.1,
            features={"book_imbalance_l5": np.random.randn()},
        ))

    runner = WalkForwardRunner(
        engine=BacktestEngine(cost_model=CryptoPerpCostModel()),
        train_days=3,
        test_days=1,
        step_days=1,
    )

    strategy_factory = lambda: SingleFactorStrategy(factor_name="book_imbalance_l5")
    result = runner.run(strategy_factory, bars)

    assert "composite_score" in result
    assert "n_windows" in result
    assert result["n_windows"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research/test_walk_forward.py -v`
Expected: FAIL

- [ ] **Step 3: Implement walk-forward validation**

```python
# research/backtest/walk_forward.py
"""Walk-forward validation to prevent overfitting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from research.backtest.engine import BacktestEngine, BarData, Strategy
from research.backtest.scorer import Scorer


def generate_windows(
    timestamps: Sequence[str],
    train_days: int = 7,
    test_days: int = 2,
    step_days: int = 2,
) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """Generate (train_range, test_range) tuples from timestamp sequence.

    Returns list of ((train_start, train_end), (test_start, test_end)).
    """
    # Extract unique dates
    dates = sorted(set(ts[:10] for ts in timestamps))
    if len(dates) < train_days + test_days:
        return []

    windows = []
    i = 0
    while i + train_days + test_days <= len(dates):
        train_start = dates[i]
        train_end = dates[i + train_days - 1]
        test_start = dates[i + train_days]
        test_end = dates[min(i + train_days + test_days - 1, len(dates) - 1)]

        windows.append(((train_start, train_end), (test_start, test_end)))
        i += step_days

    return windows


@dataclass
class WalkForwardRunner:
    """Run strategy across walk-forward windows and aggregate results."""

    engine: BacktestEngine
    train_days: int = 7
    test_days: int = 2
    step_days: int = 2

    def run(
        self,
        strategy_factory: Callable[[], Strategy],
        bars: Sequence[BarData],
        initial_capital: float = 10000.0,
    ) -> dict[str, float]:
        """Run walk-forward validation. strategy_factory creates a fresh strategy per window."""
        timestamps = [b.ts for b in bars]
        windows = generate_windows(timestamps, self.train_days, self.test_days, self.step_days)

        if not windows:
            return {"composite_score": 0.0, "n_windows": 0}

        scorer = Scorer()
        all_scores: list[dict[str, float]] = []

        for (train_start, train_end), (test_start, test_end) in windows:
            # Filter bars for test window only (train could be used for parameter fitting later)
            test_bars = [b for b in bars if test_start <= b.ts[:10] <= test_end]
            if not test_bars:
                continue

            strategy = strategy_factory()
            result = self.engine.run(strategy, test_bars, initial_capital)
            metrics = scorer.score(result.equity_curve, result.trades)
            all_scores.append(metrics)

        if not all_scores:
            return {"composite_score": 0.0, "n_windows": 0}

        # Average metrics across windows
        avg_metrics: dict[str, float] = {"n_windows": len(all_scores)}
        for key in all_scores[0]:
            vals = [s[key] for s in all_scores if isinstance(s.get(key), (int, float))]
            if vals:
                avg_metrics[key] = sum(vals) / len(vals)

        return avg_metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research/test_walk_forward.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add research/backtest/walk_forward.py tests/test_research/test_walk_forward.py
git commit -m "feat(research): add walk-forward validation for overfitting prevention"
```

---

### Task 19: Backtest CLI — run_backtest.py (updated)

**Files:**
- Create: `research/scripts/run_backtest.py`

- [ ] **Step 1: Implement run_backtest CLI**

```python
# research/scripts/run_backtest.py
"""CLI: Run backtests with different strategies."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import polars as pl


def load_bars_from_parquet(data_dir: Path, timeframe: str = "15m"):
    """Load Parquet files and convert to BarData sequence."""
    from research.backtest.engine import BarData

    feature_dir = data_dir / f"features_{timeframe}"
    files = sorted(feature_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files in {feature_dir}")

    df = pl.concat([pl.read_parquet(f) for f in files]).sort(["ts", "symbol"])

    bars = []
    for row in df.iter_rows(named=True):
        features = {k: v for k, v in row.items()
                    if k not in ("ts", "symbol", "mid_px", "top_depth_usd", "funding_rate")
                    and v is not None and isinstance(v, (int, float))}
        bars.append(BarData(
            ts=row["ts"],
            symbol=row["symbol"],
            mid_px=row.get("mid_px", 0.0) or 0.0,
            top_depth_usd=row.get("top_depth_usd", 500000.0) or 500000.0,
            funding_rate=row.get("funding_rate", 0.0) or 0.0,
            features=features,
        ))
    return bars


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--strategy", required=True,
                        choices=["rule_based", "single_factor", "weighted_factor"],
                        help="Strategy type")
    parser.add_argument("--factor", help="Factor name (for single_factor)")
    parser.add_argument("--factors", help="Factor:weight pairs (for weighted_factor)")
    parser.add_argument("--data-dir", default="research/data/parquet")
    parser.add_argument("--output-dir", default="research/reports/backtest")
    parser.add_argument("--initial-capital", type=float, default=10000.0)
    parser.add_argument("--walk-forward", help="Walk-forward params: train=7d,test=2d")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    bars = load_bars_from_parquet(data_dir)
    print(f"Loaded {len(bars)} bars")

    # Build strategy
    if args.strategy == "rule_based":
        from research.backtest.rule_based_strategy import RuleBasedStrategy
        strategy = RuleBasedStrategy()
    elif args.strategy == "single_factor":
        from research.backtest.strategies import SingleFactorStrategy
        strategy = SingleFactorStrategy(factor_name=args.factor)
    elif args.strategy == "weighted_factor":
        factor_weights = {}
        for pair in args.factors.split(","):
            name, weight = pair.split(":")
            factor_weights[name.strip()] = float(weight)
        from research.backtest.strategies import WeightedFactorStrategy
        strategy = WeightedFactorStrategy(factor_weights=factor_weights)
    else:
        raise ValueError(f"Unknown strategy: {args.strategy}")

    # Run backtest
    from research.backtest.engine import BacktestEngine
    from research.backtest.cost_model import CryptoPerpCostModel
    from research.backtest.scorer import Scorer

    engine = BacktestEngine(cost_model=CryptoPerpCostModel())
    result = engine.run(strategy=strategy, bars=bars, initial_capital=args.initial_capital)

    # Score
    scorer = Scorer()
    metrics = scorer.score(result.equity_curve, result.trades)

    # Save results
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir) / f"{args.strategy}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "summary.json", "w") as f:
        json.dump(metrics, f, indent=2)

    eq_df = pl.DataFrame({"equity": result.equity_curve})
    eq_df.write_parquet(out_dir / "equity_curve.parquet")

    if len(result.trades) > 0:
        result.trades.write_parquet(out_dir / "trades.parquet")

    print(f"\nResults saved to {out_dir}/")
    print(f"  Composite Score: {metrics['composite_score']:.1f}/100")
    print(f"  Sharpe: {metrics['sharpe']:.2f}")
    print(f"  Max Drawdown: {metrics['max_drawdown']:.2%}")
    print(f"  Win Rate: {metrics['win_rate']:.2%}")
    print(f"  Net PnL: {metrics['net_pnl_pct']:.2%}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI help runs**

Run: `python research/scripts/run_backtest.py --help`
Expected: Shows usage without error

- [ ] **Step 3: Commit**

```bash
git add research/scripts/run_backtest.py
git commit -m "feat(research): add run_backtest.py CLI script"
```

---

## Chunk 4: Parameter Optimizer (Feature Factory Reuse)

### Task 20: Copy and adapt feature_factory agent modules

**Files:**
- Create: `research/optimizer/search_space.py` (new, crypto-specific)
- Copy+adapt: `research/optimizer/param_generator.py` (from feature_factory)
- Copy+adapt: `research/optimizer/executor.py` (from feature_factory)
- Copy+adapt: `research/optimizer/results_store.py` (from feature_factory)
- Copy+adapt: `research/optimizer/evaluator.py` (from feature_factory)

**Context:**
- Source: `/home/gordonyang/workspace/myproject/auction-strategy/feature_factory/analysis/agent/`
- Reuse: LLM-driven parameter search framework, saturation detection, multi-worker execution
- Adapt: search space (crypto factors), evaluation metrics (sharpe/drawdown), execution target (run_backtest.py)

- [ ] **Step 1: Create crypto-specific search_space.py**

```python
# research/optimizer/search_space.py
"""Search space definition for crypto factor optimization."""
from __future__ import annotations

from research.factors.registry import FACTOR_FAMILIES

SEARCH_DIMENSIONS = {
    "active_factors": {
        "families": {k: [name for name, _ in v] for k, v in FACTOR_FAMILIES.items()},
        "select_mode": "family",
    },
    "signal_params": {
        "confidence_threshold": {"min": 0.3, "max": 0.8, "step": 0.05},
        "signal_delta_threshold": {"min": 0.03, "max": 0.15, "step": 0.01},
        "smooth_alpha": {"min": 0.1, "max": 0.6, "step": 0.05},
    },
    "position_params": {
        "max_gross": {"min": 0.15, "max": 0.50, "step": 0.05},
        "max_net": {"min": 0.10, "max": 0.35, "step": 0.05},
        "turnover_cap": {"min": 0.02, "max": 0.10, "step": 0.01},
        "profit_target_bps": {"min": 10, "max": 50, "step": 5},
        "stop_loss_bps": {"min": 15, "max": 80, "step": 5},
    },
    "timing_params": {
        "min_trade_interval_min": {"min": 15, "max": 120, "step": 15},
        "position_max_age_min": {"min": 15, "max": 120, "step": 15},
        "max_trades_per_day": {"min": 5, "max": 30, "step": 5},
    },
    "layer1_params": {
        "bearish_ret_1h_threshold": {"min": -0.01, "max": -0.002, "step": 0.001},
        "trending_strength_threshold": {"min": 0.5, "max": 2.0, "step": 0.25},
        "layer1_debounce_min": {"min": 30, "max": 90, "step": 15},
        "bearish_regime_long_block": [True, False],
    },
}

GOOD_THRESHOLDS = {
    "sharpe": 1.0,
    "max_drawdown": 0.10,
    "win_rate": 0.45,
    "profit_factor": 1.3,
    "net_pnl_positive": True,
}
```

- [ ] **Step 2: Copy and adapt executor.py**

Copy from feature_factory, then modify:
- Change subprocess call from `analyze_factor_pool.py` to `research/scripts/run_backtest.py`
- Adapt output parsing to read `summary.json` instead of parsing stdout

```bash
cp /home/gordonyang/workspace/myproject/auction-strategy/feature_factory/analysis/agent/executor.py \
   research/optimizer/executor.py
```

Then edit `research/optimizer/executor.py`:
- Replace the subprocess command to call `python research/scripts/run_backtest.py`
- Change output parsing to read the JSON summary file
- Update import paths

- [ ] **Step 3: Copy and adapt results_store.py**

```bash
cp /home/gordonyang/workspace/myproject/auction-strategy/feature_factory/analysis/agent/results_store.py \
   research/optimizer/results_store.py
```

Then edit:
- Change metric field names: `day_win_t1` → `win_rate`, `max_dd_t1` → `max_drawdown`, `avg_ret_t1` → `sharpe`
- Update saturation thresholds to use `GOOD_THRESHOLDS` from `search_space.py`
- Update import paths

- [ ] **Step 4: Copy and adapt param_generator.py**

```bash
cp /home/gordonyang/workspace/myproject/auction-strategy/feature_factory/analysis/agent/param_generator.py \
   research/optimizer/param_generator.py
```

Then edit:
- Update LLM prompt templates to describe crypto perpetual futures context instead of A-share auction
- Reference `SEARCH_DIMENSIONS` from `search_space.py`
- Update import paths

- [ ] **Step 5: Copy and adapt evaluator.py**

```bash
cp /home/gordonyang/workspace/myproject/auction-strategy/feature_factory/analysis/agent/evaluator.py \
   research/optimizer/evaluator.py
```

Then edit:
- Change `is_good_strategy()` to use `GOOD_THRESHOLDS` (sharpe > 1.0, max_dd < 0.10, etc.)
- Change quality score formula: `score = sharpe - 2.0 * max_drawdown`
- Update metric parsing to read from `summary.json`

- [ ] **Step 6: Commit**

```bash
git add research/optimizer/
git commit -m "feat(research): port feature_factory optimizer framework for crypto param search"
```

---

### Task 21: Search CLI — search_params.py

**Files:**
- Create: `research/scripts/search_params.py`

- [ ] **Step 1: Implement search_params CLI**

```python
# research/scripts/search_params.py
"""CLI: LLM-driven parameter optimization search."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LLM-driven parameter search")
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--data-dir", default="research/data/parquet")
    parser.add_argument("--output-dir", default="research/reports/optimizer")
    parser.add_argument("--holdout", help="Holdout date range for OOS validation (YYYY-MM-DD:YYYY-MM-DD)")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from research.optimizer.search_space import SEARCH_DIMENSIONS, GOOD_THRESHOLDS
    from research.optimizer.results_store import ResultsStore
    from research.optimizer.param_generator import ParamGenerator
    from research.optimizer.executor import ExperimentExecutor
    from research.optimizer.evaluator import CryptoEvaluator

    store = ResultsStore(output_dir=output_dir)
    generator = ParamGenerator(search_dims=SEARCH_DIMENSIONS)
    executor = ExperimentExecutor(
        data_dir=args.data_dir,
        num_workers=args.num_workers,
    )
    evaluator = CryptoEvaluator(thresholds=GOOD_THRESHOLDS)

    print(f"Starting parameter search with {args.num_workers} workers, max {args.max_rounds} rounds")

    for round_num in range(1, args.max_rounds + 1):
        print(f"\n--- Round {round_num}/{args.max_rounds} ---")

        # Generate parameter combinations
        params_batch = generator.generate(store.get_history())

        # Execute backtests
        results = executor.run_batch(params_batch)

        # Evaluate and store
        for params, result in zip(params_batch, results):
            evaluation = evaluator.evaluate(result)
            store.add(params, evaluation)
            if evaluation.get("is_good"):
                print(f"  Good strategy found: sharpe={evaluation.get('sharpe', 0):.2f}, "
                      f"max_dd={evaluation.get('max_drawdown', 0):.2%}")

        # Check saturation
        if store.is_saturated():
            print("Search saturated — enough good strategies found")
            break

    # Save final results
    store.save_summary()
    print(f"\nSearch complete. Results in {output_dir}/")
    print(f"  Total experiments: {store.total_count()}")
    print(f"  Good strategies: {store.good_count()}")

    # Show best parameters
    best = store.get_best()
    if best:
        print(f"  Best composite score: {best.get('composite_score', 0):.1f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI help runs**

Run: `python research/scripts/search_params.py --help`
Expected: Shows usage without error

- [ ] **Step 3: Commit**

```bash
git add research/scripts/search_params.py
git commit -m "feat(research): add search_params.py CLI for LLM-driven optimization"
```

---

### Task 22: Integration test — end-to-end pipeline

**Files:**
- Create: `tests/test_research/test_integration.py`

- [ ] **Step 1: Write integration test for export → evaluate → backtest pipeline**

```python
# tests/test_research/test_integration.py
"""Integration test: export synthetic data → evaluate factors → run backtest."""
import json
import numpy as np
import polars as pl
import pytest
from pathlib import Path


@pytest.fixture
def research_dir(tmp_path):
    """Create synthetic 15m feature data mimicking real export output."""
    features_dir = tmp_path / "features_15m"
    features_dir.mkdir(parents=True)

    np.random.seed(42)
    n_bars = 200
    n_symbols = 2
    total = n_bars * n_symbols

    # Generate synthetic data
    symbols = ["BTC", "ETH"] * n_bars
    ts = []
    for i in range(n_bars):
        for s in ["BTC", "ETH"]:
            ts.append(f"2026-03-{15 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00Z")

    base_price_btc = 65000 + np.cumsum(np.random.randn(n_bars) * 50)
    base_price_eth = 3200 + np.cumsum(np.random.randn(n_bars) * 25)
    mid_px = []
    for i in range(n_bars):
        mid_px.append(base_price_btc[i])
        mid_px.append(base_price_eth[i])

    # Generate correlated factor (book_imbalance_l5 → future return)
    bi_vals = np.random.randn(total) * 0.3
    # Make some factors predictive
    future_component = np.roll(np.random.randn(total) * 0.001, -2)

    df = pl.DataFrame({
        "ts": ts,
        "symbol": symbols,
        "mid_px": mid_px,
        "book_imbalance_l5": bi_vals.tolist(),
        "spread_bps": (np.random.rand(total) * 5).tolist(),
        "funding_rate": (np.random.randn(total) * 0.0001).tolist(),
        "top_depth_usd": (np.random.rand(total) * 1000000 + 100000).tolist(),
        "aggr_delta_5m": (np.random.randn(total) * 100).tolist(),
    })

    # Add forward returns
    from research.data.exporter import build_forward_returns
    df = build_forward_returns(df, horizons=[1, 2, 4])

    df.write_parquet(features_dir / "2026-03-15.parquet")
    return tmp_path


def test_factor_evaluation_pipeline(research_dir):
    """Evaluate factors and produce summary."""
    from research.factors.evaluator import evaluate_all_factors

    df = pl.read_parquet(research_dir / "features_15m" / "2026-03-15.parquet")
    summary = evaluate_all_factors(
        df,
        factor_cols=["book_imbalance_l5", "spread_bps", "aggr_delta_5m"],
        return_cols=["ret_fwd_15m", "ret_fwd_30m", "ret_fwd_1h"],
    )

    assert len(summary) == 9  # 3 factors × 3 horizons
    assert "icir" in summary.columns


def test_backtest_pipeline(research_dir):
    """Run a backtest with SingleFactorStrategy on synthetic data."""
    from research.scripts.run_backtest import load_bars_from_parquet
    from research.backtest.engine import BacktestEngine
    from research.backtest.cost_model import CryptoPerpCostModel
    from research.backtest.strategies import SingleFactorStrategy
    from research.backtest.scorer import Scorer

    bars = load_bars_from_parquet(research_dir)
    assert len(bars) > 0

    strategy = SingleFactorStrategy(factor_name="book_imbalance_l5")
    engine = BacktestEngine(cost_model=CryptoPerpCostModel())
    result = engine.run(strategy=strategy, bars=bars, initial_capital=10000.0)

    assert len(result.equity_curve) == len(bars)

    scorer = Scorer()
    metrics = scorer.score(result.equity_curve, result.trades)
    assert "composite_score" in metrics
    assert 0 <= metrics["composite_score"] <= 100
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_research/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_research/test_integration.py
git commit -m "test(research): add end-to-end integration tests for research pipeline"
```

---

### Task 23: Final — run all tests and verify

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/test_research/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify project structure matches spec**

```bash
find research/ -type f -name "*.py" | sort
```

Expected output should include all planned files:
- `research/__init__.py`
- `research/data/__init__.py`, `exporter.py`, `db.py`
- `research/factors/__init__.py`, `registry.py`, `evaluator.py`, `correlation.py`, `report.py`
- `research/backtest/__init__.py`, `engine.py`, `cost_model.py`, `scorer.py`, `strategies.py`
- `research/optimizer/__init__.py`, `search_space.py`, `param_generator.py`, `executor.py`, `results_store.py`, `evaluator.py`
- `research/scripts/__init__.py`, `export_data.py`, `eval_factors.py`, `run_backtest.py`, `search_params.py`

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(research): complete research system v1 — factor eval, backtest, optimizer"
```
