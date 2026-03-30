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

    if args.with_forward_returns:
        features_dir = output_dir / "features_15m"
        parquet_files = sorted(features_dir.glob("*.parquet"))
        if parquet_files:
            df = pl.concat([pl.read_parquet(f) for f in parquet_files])
            df = build_forward_returns(df)
            for dt_str, group_df in df.group_by(pl.col("ts").str.slice(0, 10).alias("date")):
                dt_val = dt_str[0] if isinstance(dt_str, tuple) else dt_str
                out_path = features_dir / f"{dt_val}.parquet"
                group_df.write_parquet(out_path)
            print(f"  Forward returns computed for {len(parquet_files)} files")

    print("Export complete.")


if __name__ == "__main__":
    main()
