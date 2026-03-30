"""CLI for factor evaluation: IC, ICIR, quantile returns, correlation."""
from __future__ import annotations
import argparse
import os
import sys

import polars as pl


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate factors from parquet feature files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        default="research/data/parquet",
        help="Root directory containing parquet feature subdirectories.",
    )
    parser.add_argument(
        "--timeframe",
        choices=["1m", "15m", "1h"],
        default="15m",
        help="Feature timeframe to evaluate.",
    )
    parser.add_argument(
        "--output",
        default="research/reports/factor_eval",
        help="Output directory for reports.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print evaluation summary to stdout.",
    )
    args = parser.parse_args()

    features_dir = os.path.join(args.data_dir, f"features_{args.timeframe}")
    if not os.path.isdir(features_dir):
        print(f"ERROR: features directory not found: {features_dir}", file=sys.stderr)
        sys.exit(1)

    # Load all parquet files in the features directory
    parquet_files = [
        os.path.join(features_dir, f)
        for f in os.listdir(features_dir)
        if f.endswith(".parquet")
    ]
    if not parquet_files:
        print(f"ERROR: no parquet files found in {features_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {len(parquet_files)} parquet file(s) from {features_dir} ...")
    df = pl.concat([pl.read_parquet(p) for p in parquet_files])

    # Determine factor columns for this timeframe
    from research.factors.registry import get_factors_for_timeframe
    registry_factors = set(get_factors_for_timeframe(args.timeframe))
    factor_cols = [c for c in df.columns if c in registry_factors]

    if not factor_cols:
        print("WARNING: no registered factor columns found in data.", file=sys.stderr)

    # Evaluate all factors
    from research.factors.evaluator import evaluate_all_factors
    print(f"Evaluating {len(factor_cols)} factor(s) ...")
    eval_df = evaluate_all_factors(df, factor_cols=factor_cols)

    os.makedirs(args.output, exist_ok=True)

    # Save summary CSV
    from research.factors.report import save_summary_csv, save_ic_decay_plot, save_correlation_heatmap
    csv_path = save_summary_csv(eval_df, args.output)
    print(f"Summary CSV saved: {csv_path}")

    # Save IC decay plot
    plot_path = save_ic_decay_plot(eval_df, args.output)
    print(f"IC decay plot saved: {plot_path}")

    # Compute and save correlation heatmap (if factors exist)
    if factor_cols:
        from research.factors.correlation import compute_correlation_matrix, find_redundant_factors
        corr_matrix = compute_correlation_matrix(df, factor_cols)
        heatmap_path = save_correlation_heatmap(corr_matrix, factor_cols, args.output)
        print(f"Correlation heatmap saved: {heatmap_path}")

        # Build ICIR map for redundancy detection
        icir_map: dict[str, float] = {}
        if not eval_df.is_empty() and "factor" in eval_df.columns:
            for row in eval_df.iter_rows(named=True):
                f = row["factor"]
                icir_map[f] = max(abs(row.get("icir", 0.0)), icir_map.get(f, 0.0))

        redundant = find_redundant_factors(df, factor_cols, icir_map)

        # Classify factors
        if not eval_df.is_empty() and "icir" in eval_df.columns:
            effective = set(
                eval_df.filter(pl.col("icir").abs() >= 0.3)["factor"].to_list()
            )
            noise = set(factor_cols) - effective - redundant
        else:
            effective = set()
            noise = set(factor_cols)

        print(f"\nFactor classification ({args.timeframe}):")
        print(f"  Effective (|ICIR| >= 0.3): {len(effective)}")
        print(f"  Noise:                     {len(noise)}")
        print(f"  Redundant:                 {len(redundant)}")

    if args.summary and not eval_df.is_empty():
        print("\n--- Factor Evaluation Summary ---")
        with pl.Config(tbl_rows=50):
            print(eval_df)


if __name__ == "__main__":
    main()
