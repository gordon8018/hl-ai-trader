"""Export data from Redis Streams and SQLite to Parquet files."""
from __future__ import annotations

import json
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import polars as pl

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
        rows.append(row)
    return rows


_STREAM_DIR_MAP = {
    "md.features.1m": "features_1m",
    "md.features.15m": "features_15m",
    "md.features.1h": "features_1h",
}


class DataExporter:
    """Export historical data from Redis Streams and SQLite to Parquet."""

    _SQLITE_DIR_MAP = {
        "exec_reports": "trades",
        "state_snapshots": "snapshots",
    }

    def __init__(self, redis_client: Any, sqlite_path: Optional[str | Path], output_dir: str | Path):
        self.redis = redis_client
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        self.output_dir = Path(output_dir)

    def export_redis_stream(self, stream_key: str, date_range: tuple[date, date]) -> list[Path]:
        """Export a Redis stream to daily Parquet files. Incremental: skips existing dates."""
        subdir = _STREAM_DIR_MAP.get(stream_key, stream_key.replace(".", "_"))
        out_dir = self.output_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)

        start_date, end_date = date_range
        start_ms = int(datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime.combine(end_date, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc).timestamp() * 1000)

        messages = self.redis.xrange(stream_key, min_id=str(start_ms), max_id=str(end_ms))

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

        df = pl.DataFrame(all_rows)
        df = df.with_columns(pl.col("ts").str.slice(0, 10).alias("date"))

        written: list[Path] = []
        for dt_str, group_df in df.group_by(["date"]):
            dt_val = dt_str[0] if isinstance(dt_str, tuple) else dt_str
            out_path = out_dir / f"{dt_val}.parquet"
            if out_path.exists():
                continue
            group_df.drop("date").write_parquet(out_path)
            written.append(out_path)
        return written

    def export_sqlite_table(self, table: str, date_range: tuple[date, date]) -> list[Path]:
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
        df = df.with_columns(pl.col("ts").str.slice(0, 10).alias("date"))

        written: list[Path] = []
        for dt_str, group_df in df.group_by(["date"]):
            dt_val = dt_str[0] if isinstance(dt_str, tuple) else dt_str
            out_path = out_dir / f"{dt_val}.parquet"
            if out_path.exists():
                continue
            group_df.drop("date").write_parquet(out_path)
            written.append(out_path)
        return written


_HORIZON_NAMES = {1: "15m", 2: "30m", 4: "1h", 8: "2h", 16: "4h"}


def build_forward_returns(df: pl.DataFrame, horizons: Sequence[int] = (1, 2, 4, 8, 16)) -> pl.DataFrame:
    """Compute forward returns per symbol. 1 bar = 15 minutes."""
    result = df.sort(["symbol", "ts"])
    for h in horizons:
        col_name = f"ret_fwd_{_HORIZON_NAMES.get(h, f'{h}bar')}"
        result = result.with_columns(
            ((pl.col("mid_px").shift(-h).over("symbol") - pl.col("mid_px")) / pl.col("mid_px")).alias(col_name)
        )
    return result
