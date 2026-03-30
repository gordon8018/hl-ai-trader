"""Walk-forward validation to prevent overfitting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from research.backtest.engine import BacktestEngine, BarData, Strategy
from research.backtest.scorer import Scorer


def generate_windows(
    timestamps: Sequence[str],
    train_days: int = 7,
    test_days: int = 2,
    step_days: int = 2,
) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """Generate (train_range, test_range) tuples from timestamp sequence.

    Returns list of ((train_start, train_end), (test_start, test_end)).
    """
    # Extract unique dates
    dates = sorted(set(ts[:10] for ts in timestamps))
    if len(dates) < train_days + test_days:
        return []

    windows = []
    i = 0
    while i + train_days + test_days <= len(dates):
        train_start = dates[i]
        train_end = dates[i + train_days - 1]
        test_start = dates[i + train_days]
        test_end = dates[min(i + train_days + test_days - 1, len(dates) - 1)]

        windows.append(((train_start, train_end), (test_start, test_end)))
        i += step_days

    return windows


@dataclass
class WalkForwardRunner:
    """Run strategy across walk-forward windows and aggregate results."""

    engine: BacktestEngine
    train_days: int = 7
    test_days: int = 2
    step_days: int = 2

    def run(
        self,
        strategy_factory: Callable[[], Strategy],
        bars: Sequence[BarData],
        initial_capital: float = 10000.0,
    ) -> dict[str, float]:
        """Run walk-forward validation. strategy_factory creates a fresh strategy per window."""
        timestamps = [b.ts for b in bars]
        windows = generate_windows(timestamps, self.train_days, self.test_days, self.step_days)

        if not windows:
            return {"composite_score": 0.0, "n_windows": 0}

        scorer = Scorer()
        all_scores: list[dict[str, float]] = []

        for (train_start, train_end), (test_start, test_end) in windows:
            # Filter bars for test window only (train could be used for parameter fitting later)
            test_bars = [b for b in bars if test_start <= b.ts[:10] <= test_end]
            if not test_bars:
                continue

            strategy = strategy_factory()
            result = self.engine.run(strategy, test_bars, initial_capital)
            metrics = scorer.score(result.equity_curve, result.trades)
            all_scores.append(metrics)

        if not all_scores:
            return {"composite_score": 0.0, "n_windows": 0}

        # Average metrics across windows
        avg_metrics: dict[str, float] = {"n_windows": len(all_scores)}
        for key in all_scores[0]:
            vals = [s[key] for s in all_scores if isinstance(s.get(key), (int, float))]
            if vals:
                avg_metrics[key] = sum(vals) / len(vals)

        return avg_metrics
