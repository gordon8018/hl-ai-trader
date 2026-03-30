"""CLI: Download historical data from Hyperliquid and build research Parquet files."""
from __future__ import annotations

import argparse
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from research.data.hl_client import HLClient
from research.data.exporter import build_forward_returns


def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _date_to_ms(d: date, end_of_day: bool = False) -> int:
    if end_of_day:
        dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    return _dt_to_ms(dt)


def download_candles(client: HLClient, coin: str, start: date, end: date) -> pl.DataFrame:
    """Download 15m candles and convert to DataFrame."""
    start_ms = _date_to_ms(start)
    end_ms = _date_to_ms(end, end_of_day=True)

    print(f"  Downloading 15m candles for {coin}...")
    candles = client.candles_range(coin, "15m", start_ms, end_ms)
    print(f"    Got {len(candles)} candles")

    if not candles:
        return pl.DataFrame()

    rows = []
    for c in candles:
        ts_ms = int(c["t"])
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        rows.append({
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "symbol": coin,
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
            "trade_count": int(c["n"]),
        })

    return pl.DataFrame(rows).sort("ts")


def download_funding(client: HLClient, coin: str, start: date, end: date) -> pl.DataFrame:
    """Download funding rate history."""
    start_ms = _date_to_ms(start)
    end_ms = _date_to_ms(end, end_of_day=True)

    print(f"  Downloading funding history for {coin}...")
    records = client.funding_history(coin, start_ms, end_ms)
    print(f"    Got {len(records)} funding records")

    if not records:
        return pl.DataFrame()

    rows = []
    for r in records:
        ts = datetime.fromtimestamp(int(r["time"]) / 1000, tz=timezone.utc)
        rows.append({
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "symbol": coin,
            "funding_rate": float(r["fundingRate"]),
            "premium": float(r.get("premium", 0)),
        })

    return pl.DataFrame(rows).sort("ts")


def compute_features(candle_df: pl.DataFrame, funding_df: pl.DataFrame) -> pl.DataFrame:
    """Compute research features from raw OHLCV + funding data.

    Input: 15m candle data with columns: ts, symbol, open, high, low, close, volume, trade_count
    Output: Feature DataFrame matching research Parquet schema
    """
    if candle_df.is_empty():
        return pl.DataFrame()

    df = candle_df.sort(["symbol", "ts"])

    # Mid price = close (best proxy from candle data)
    df = df.with_columns(pl.col("close").alias("mid_px"))

    # Returns at different horizons
    for bars, name in [(1, "ret_15m"), (2, "ret_30m"), (4, "ret_1h"), (16, "ret_4h"), (96, "ret_24h")]:
        df = df.with_columns(
            ((pl.col("close") - pl.col("close").shift(bars).over("symbol"))
             / pl.col("close").shift(bars).over("symbol")).alias(name)
        )

    # Volatility = rolling std of 15m returns
    df = df.with_columns(
        pl.col("ret_15m").rolling_std(window_size=4, min_periods=2).over("symbol").alias("vol_15m"),
        pl.col("ret_15m").rolling_std(window_size=16, min_periods=4).over("symbol").alias("vol_1h"),
        pl.col("ret_15m").rolling_std(window_size=64, min_periods=16).over("symbol").alias("vol_4h"),
    )

    # Trends: sign of returns
    for ret_col, trend_col in [("ret_15m", "trend_15m"), ("ret_1h", "trend_1h"), ("ret_4h", "trend_4h")]:
        df = df.with_columns(
            pl.when(pl.col(ret_col) > 0.001).then(1)
            .when(pl.col(ret_col) < -0.001).then(-1)
            .otherwise(0)
            .alias(trend_col)
        )

    # Trend agree
    df = df.with_columns(
        pl.when(
            (pl.col("trend_15m") == pl.col("trend_1h")) &
            (pl.col("trend_1h") == pl.col("trend_4h")) &
            (pl.col("trend_15m") != 0)
        ).then(pl.col("trend_15m"))
        .otherwise(0)
        .alias("trend_agree")
    )

    # Trend strength
    df = df.with_columns(
        (pl.col("ret_15m").abs() / pl.col("vol_15m").clip(lower_bound=1e-8))
        .alias("trend_strength_15m")
    )

    # Vol spike: vol_15m > 2 * rolling median
    df = df.with_columns(
        pl.when(
            pl.col("vol_15m") > 2.0 * pl.col("vol_15m").rolling_median(window_size=96, min_periods=10).over("symbol")
        )
        .then(1).otherwise(0).alias("vol_spike")
    )

    # Vol regime: -1 (low), 0 (normal), 1 (high)
    df = df.with_columns(
        pl.when(pl.col("vol_spike") == 1).then(1)
        .when(
            pl.col("vol_15m") < 0.5 * pl.col("vol_15m").rolling_median(window_size=96, min_periods=10).over("symbol")
        )
        .then(-1)
        .otherwise(0)
        .alias("vol_regime")
    )

    # Spread proxy from OHLCV: (high - low) / close * 10000
    df = df.with_columns(
        ((pl.col("high") - pl.col("low")) / pl.col("close") * 10000).alias("spread_bps")
    )

    # Volume-based features
    df = df.with_columns([
        pl.col("volume").alias("trade_volume_1m"),
        pl.col("trade_count").alias("trade_count_1m"),
    ])

    # Join funding data (forward-fill to 15m bars)
    if not funding_df.is_empty():
        funding_sub = funding_df.select(["ts", "symbol", "funding_rate"]).rename({"funding_rate": "_fr"})
        df = df.join_asof(
            funding_sub.sort("ts"),
            on="ts",
            by="symbol",
            strategy="backward",
        )
        df = df.with_columns(
            pl.col("_fr").fill_null(0.0).alias("funding_rate")
        ).drop("_fr")
    else:
        df = df.with_columns(pl.lit(0.0).alias("funding_rate"))

    # Basis proxy from funding premium (if we had it; use funding_rate as proxy)
    df = df.with_columns(
        (pl.col("funding_rate") * 10000).alias("basis_bps")
    )

    # Cross-market features: BTC/ETH returns per symbol
    btc_rets = df.filter(pl.col("symbol") == "BTC").select([
        "ts",
        pl.col("ret_15m").alias("btc_ret_15m"),
        pl.col("ret_1h").alias("btc_ret_1h"),
    ])
    eth_rets = df.filter(pl.col("symbol") == "ETH").select([
        "ts",
        pl.col("ret_15m").alias("eth_ret_15m"),
    ])

    if not btc_rets.is_empty():
        df = df.join(btc_rets, on="ts", how="left", coalesce=True)
    else:
        df = df.with_columns([
            pl.lit(None).cast(pl.Float64).alias("btc_ret_15m"),
            pl.lit(None).cast(pl.Float64).alias("btc_ret_1h"),
        ])

    if not eth_rets.is_empty():
        df = df.join(eth_rets, on="ts", how="left", coalesce=True)
    else:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("eth_ret_15m"))

    # Select final columns (drop raw OHLCV)
    keep_cols = [
        "ts", "symbol", "mid_px",
        "ret_15m", "ret_30m", "ret_1h", "ret_4h", "ret_24h",
        "vol_15m", "vol_1h", "vol_4h",
        "trend_15m", "trend_1h", "trend_4h", "trend_agree", "trend_strength_15m",
        "vol_spike", "vol_regime",
        "spread_bps",
        "trade_volume_1m", "trade_count_1m",
        "funding_rate", "basis_bps",
        "btc_ret_15m", "eth_ret_15m",
    ]
    # Only select columns that exist
    existing = [c for c in keep_cols if c in df.columns]
    return df.select(existing)


def save_daily_parquet(df: pl.DataFrame, output_dir: Path) -> int:
    """Save DataFrame as daily Parquet files. Returns count of files written."""
    if df.is_empty():
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    df = df.with_columns(pl.col("ts").str.slice(0, 10).alias("_date"))

    count = 0
    for (dt_val,), group_df in df.group_by(["_date"]):
        out_path = output_dir / f"{dt_val}.parquet"
        group_df.drop("_date").write_parquet(out_path)
        count += 1

    return count


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Download historical data from Hyperliquid"
    )
    parser.add_argument("--coins", default="BTC,ETH",
                        help="Comma-separated coin list (default: BTC,ETH)")
    parser.add_argument("--start-date", required=True,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True,
                        help="End date YYYY-MM-DD")
    parser.add_argument("--output-dir", default="research/data/parquet",
                        help="Output directory for Parquet files")
    parser.add_argument("--with-forward-returns", action="store_true", default=True,
                        help="Compute forward returns after download")
    parser.add_argument("--api-url", default="https://api.hyperliquid.xyz/info",
                        help="Hyperliquid API URL")
    args = parser.parse_args(argv)

    coins = [c.strip() for c in args.coins.split(",")]
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    output_dir = Path(args.output_dir)

    print(f"Downloading Hyperliquid data: {coins}, {start} to {end}")

    client = HLClient(base_url=args.api_url)

    try:
        # Download candles and funding for each coin
        all_candles = []
        all_funding = []

        for coin in coins:
            candle_df = download_candles(client, coin, start, end)
            if not candle_df.is_empty():
                all_candles.append(candle_df)

            funding_df = download_funding(client, coin, start, end)
            if not funding_df.is_empty():
                all_funding.append(funding_df)

        if not all_candles:
            print("No candle data downloaded. Check date range and coins.")
            return

        candle_combined = pl.concat(all_candles)
        funding_combined = pl.concat(all_funding) if all_funding else pl.DataFrame()

        print(f"\nTotal: {len(candle_combined)} candles, {len(funding_combined)} funding records")

        # Compute features
        print("Computing features...")
        features_df = compute_features(candle_combined, funding_combined)
        print(f"  {len(features_df)} feature rows, {len(features_df.columns)} columns")

        # Add forward returns
        if args.with_forward_returns and not features_df.is_empty():
            print("Computing forward returns...")
            features_df = build_forward_returns(features_df)

        # Save to daily Parquet
        features_dir = output_dir / "features_15m"
        n_files = save_daily_parquet(features_df, features_dir)
        print(f"\nSaved {n_files} daily Parquet files to {features_dir}/")

        # Print summary
        date_range = features_df["ts"].str.slice(0, 10)
        print(f"  Date range: {date_range.min()} to {date_range.max()}")
        print(f"  Symbols: {features_df['symbol'].unique().to_list()}")
        print(f"  Bars per symbol per day: ~{len(features_df) // n_files // len(coins) if n_files > 0 else 0}")

    finally:
        client.close()

    print("\nDownload complete.")


if __name__ == "__main__":
    main()
