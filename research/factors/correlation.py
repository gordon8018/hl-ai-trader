"""Factor correlation analysis and redundancy detection."""
from __future__ import annotations
import numpy as np
import polars as pl


def compute_correlation_matrix(df: pl.DataFrame, factor_cols: list[str]) -> np.ndarray:
    data = df.select(factor_cols).to_numpy()
    valid_mask = ~np.isnan(data).any(axis=1)
    data = data[valid_mask]
    if len(data) < 3:
        return np.eye(len(factor_cols))
    return np.corrcoef(data, rowvar=False)


def find_redundant_factors(df: pl.DataFrame, factor_cols: list[str], icir_map: dict[str, float], threshold: float = 0.8) -> set[str]:
    corr_matrix = compute_correlation_matrix(df, factor_cols)
    redundant = set()
    for i in range(len(factor_cols)):
        for j in range(i + 1, len(factor_cols)):
            if abs(corr_matrix[i, j]) > threshold:
                fi, fj = factor_cols[i], factor_cols[j]
                if abs(icir_map.get(fi, 0)) >= abs(icir_map.get(fj, 0)):
                    redundant.add(fj)
                else:
                    redundant.add(fi)
    return redundant
