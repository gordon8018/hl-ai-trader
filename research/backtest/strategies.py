"""Built-in backtest strategies for factor evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from research.backtest.engine import BarData


@dataclass
class SingleFactorStrategy:
    """Go long/short based on a single factor value exceeding percentile thresholds.

    Use `from_bars()` to auto-calibrate thresholds from historical data.
    """

    factor_name: str
    long_threshold: float = 0.5
    short_threshold: float = -0.5
    max_weight: float = 0.3

    @classmethod
    def from_bars(
        cls,
        factor_name: str,
        bars: Sequence[BarData],
        long_pct: float = 0.7,
        short_pct: float = 0.3,
        max_weight: float = 0.3,
    ) -> "SingleFactorStrategy":
        """Create strategy with thresholds calibrated from bar data percentiles."""
        vals = [b.features.get(factor_name, 0.0) for b in bars
                if b.features.get(factor_name) is not None]
        if vals:
            arr = np.array(vals)
            long_th = float(np.percentile(arr, long_pct * 100))
            short_th = float(np.percentile(arr, short_pct * 100))
        else:
            long_th, short_th = 0.5, -0.5
        return cls(factor_name=factor_name, long_threshold=long_th,
                   short_threshold=short_th, max_weight=max_weight)

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
    """Weighted combination of multiple factors to generate z-score signal.

    Use `from_bars()` to auto-calibrate normalization from historical data.
    """

    factor_weights: dict[str, float]
    long_threshold: float = 0.5    # in z-score units after normalization
    short_threshold: float = -0.5
    max_weight: float = 0.2
    # Per-factor normalization (mean, std)
    _norm: dict[str, tuple[float, float]] = field(default_factory=dict)

    @classmethod
    def from_bars(
        cls,
        factor_weights: dict[str, float],
        bars: Sequence[BarData],
        long_threshold: float = 0.5,
        short_threshold: float = -0.5,
        max_weight: float = 0.2,
    ) -> "WeightedFactorStrategy":
        """Create strategy with z-score normalization calibrated from bar data."""
        norm = {}
        for factor in factor_weights:
            vals = [b.features.get(factor, 0.0) for b in bars
                    if b.features.get(factor) is not None]
            if vals:
                arr = np.array(vals)
                norm[factor] = (float(np.mean(arr)), max(float(np.std(arr)), 1e-10))
            else:
                norm[factor] = (0.0, 1.0)
        return cls(factor_weights=factor_weights, long_threshold=long_threshold,
                   short_threshold=short_threshold, max_weight=max_weight, _norm=norm)

    def on_bar(self, bar: BarData) -> dict[str, float]:
        signal = 0.0
        total_weight = 0.0
        for factor, w in self.factor_weights.items():
            val = bar.features.get(factor)
            if val is not None:
                # Z-score normalize if we have stats
                if factor in self._norm:
                    mean, std = self._norm[factor]
                    val = (val - mean) / std
                signal += val * w
                total_weight += abs(w)

        if total_weight > 0:
            signal /= total_weight

        if signal > self.long_threshold:
            weight = self.max_weight
        elif signal < self.short_threshold:
            weight = -self.max_weight
        else:
            weight = 0.0

        return {bar.symbol: weight}
