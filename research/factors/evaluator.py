"""Factor evaluation: IC, ICIR, quantile returns, decay analysis."""
from __future__ import annotations
from typing import Sequence
import numpy as np
import polars as pl


def compute_ic_series(df: pl.DataFrame, factor_col: str, return_col: str, window: int = 48) -> np.ndarray:
    """Compute rolling rank IC per symbol over rolling windows (handles 2-symbol case)."""
    from scipy.stats import spearmanr
    all_ics = []
    for _, sym_df in df.sort("ts").group_by(["symbol"]):
        f = sym_df[factor_col].to_numpy().astype(float)
        r = sym_df[return_col].to_numpy().astype(float)
        for i in range(0, len(f) - window + 1, window // 2):
            f_win = f[i:i + window]
            r_win = r[i:i + window]
            mask = ~(np.isnan(f_win) | np.isnan(r_win))
            if mask.sum() < 10:
                continue
            corr, _ = spearmanr(f_win[mask], r_win[mask])
            if not np.isnan(corr):
                all_ics.append(corr)
    return np.array(all_ics) if all_ics else np.array([0.0])


def compute_factor_metrics(df: pl.DataFrame, factor_col: str, return_col: str) -> dict[str, float]:
    ic_series = compute_ic_series(df, factor_col, return_col)
    if len(ic_series) == 0:
        return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0, "ic_count": 0}
    ic_mean = float(np.mean(ic_series))
    ic_std = float(np.std(ic_series))
    icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
    return {"ic_mean": ic_mean, "ic_std": ic_std, "icir": icir, "ic_count": len(ic_series)}


def compute_quantile_returns(df: pl.DataFrame, factor_col: str, return_col: str, n_quantiles: int = 5) -> pl.DataFrame:
    from scipy.stats import spearmanr
    clean = df.select([factor_col, return_col]).drop_nulls()
    if len(clean) < n_quantiles * 2:
        return pl.DataFrame({"quantile": [], "mean_return": [], "count": [], "monotonicity": []})
    clean = clean.with_columns(
        pl.col(factor_col).rank("ordinal").floordiv(pl.lit(len(clean) // n_quantiles + 1)).clip(0, n_quantiles - 1).alias("quantile")
    )
    result = clean.group_by("quantile").agg([
        pl.col(return_col).mean().alias("mean_return"),
        pl.col(return_col).count().alias("count"),
    ]).sort("quantile")
    if len(result) >= 3:
        mono_corr, _ = spearmanr(result["quantile"].to_numpy(), result["mean_return"].to_numpy())
    else:
        mono_corr = 0.0
    result = result.with_columns(pl.lit(mono_corr).alias("monotonicity"))
    return result


def compute_factor_autocorrelation(df: pl.DataFrame, factor_col: str, lag: int = 1) -> float:
    autocorrs = []
    for _, sym_df in df.sort("ts").group_by(["symbol"]):
        vals = sym_df[factor_col].drop_nulls().to_numpy()
        if len(vals) < lag + 2:
            continue
        corr = np.corrcoef(vals[:-lag], vals[lag:])[0, 1]
        if not np.isnan(corr):
            autocorrs.append(corr)
    return float(np.mean(autocorrs)) if autocorrs else 0.0


def evaluate_all_factors(df: pl.DataFrame, factor_cols: Sequence[str] | None = None, return_cols: Sequence[str] | None = None) -> pl.DataFrame:
    if return_cols is None:
        return_cols = [c for c in df.columns if c.startswith("ret_fwd_")]
    if factor_cols is None:
        from research.factors.registry import get_all_factors
        factor_cols = [c for c in df.columns if c in set(get_all_factors())]
    rows = []
    for factor in factor_cols:
        if factor not in df.columns:
            continue
        for ret_col in return_cols:
            if ret_col not in df.columns:
                continue
            metrics = compute_factor_metrics(df, factor, ret_col)
            autocorr = compute_factor_autocorrelation(df, factor)
            rows.append({"factor": factor, "horizon": ret_col.replace("ret_fwd_", ""), **metrics, "autocorrelation": autocorr})
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).sort(pl.col("icir").abs(), descending=True)
