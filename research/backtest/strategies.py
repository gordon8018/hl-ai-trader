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
