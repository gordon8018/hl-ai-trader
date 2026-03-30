"""Export data from Redis Streams and SQLite to Parquet files."""
from __future__ import annotations

from typing import Any

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
