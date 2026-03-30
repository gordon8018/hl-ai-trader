"""Factor report generation: summary CSV, IC decay plot, correlation heatmap."""
from __future__ import annotations
import os
import numpy as np
import polars as pl

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def save_summary_csv(eval_df: pl.DataFrame, output_dir: str, filename: str = "factor_summary.csv") -> str:
    """Save factor evaluation summary to CSV."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    eval_df.write_csv(path)
    return path


def save_ic_decay_plot(
    eval_df: pl.DataFrame,
    output_dir: str,
    top_n: int = 10,
    filename: str = "ic_decay.png",
) -> str:
    """Plot IC mean by horizon for the top N factors (by |ICIR|) and save to PNG."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)

    if eval_df.is_empty() or "factor" not in eval_df.columns:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.set_title("IC Decay (no data)")
        fig.savefig(path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return path

    # Pick top N factors by max |icir| across horizons
    top_factors = (
        eval_df.group_by("factor")
        .agg(pl.col("icir").abs().max().alias("max_abs_icir"))
        .sort("max_abs_icir", descending=True)
        .head(top_n)["factor"]
        .to_list()
    )

    fig, ax = plt.subplots(figsize=(12, 7))
    for factor in top_factors:
        factor_df = eval_df.filter(pl.col("factor") == factor).sort("horizon")
        if factor_df.is_empty():
            continue
        horizons = factor_df["horizon"].to_list()
        ic_means = factor_df["ic_mean"].to_list()
        ax.plot(horizons, ic_means, marker="o", label=factor)

    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_title(f"IC Decay — Top {top_n} Factors by |ICIR|")
    ax.set_xlabel("Horizon")
    ax.set_ylabel("IC Mean")
    ax.legend(loc="best", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path


def save_correlation_heatmap(
    corr_matrix: np.ndarray,
    factor_cols: list[str],
    output_dir: str,
    filename: str = "factor_correlation.png",
) -> str:
    """Save a correlation heatmap to PNG."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)

    n = len(factor_cols)
    fig_size = max(8, n * 0.4)
    fig, ax = plt.subplots(figsize=(fig_size, fig_size * 0.85))

    im = ax.imshow(corr_matrix, vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(factor_cols, rotation=90, fontsize=max(4, 8 - n // 10))
    ax.set_yticklabels(factor_cols, fontsize=max(4, 8 - n // 10))
    ax.set_title("Factor Correlation Heatmap")

    fig.tight_layout()
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path
