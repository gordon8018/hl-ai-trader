"""RuleBasedStrategy: replicates production Layer2 decision logic for baseline comparison."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from research.backtest.engine import BarData


def _parse_ts(ts: str) -> datetime:
    """Parse ISO timestamp to datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass
class PositionState:
    symbol: str
    entry_px: float
    entry_ts: str
    weight: float


@dataclass
class RuleBasedStrategy:
    """Replicate production Layer2 stateful constraints.

    Parameters match config/trading_params.json V9 defaults.
    """
    # Position management
    profit_target_bps: float = 25.0
    stop_loss_bps: float = 40.0
    position_max_age_min: int = 30
    # Trade gating
    min_trade_interval_min: int = 90
    max_trades_per_day: int = 15
    # Regime
    bearish_regime_long_block: bool = True
    bearish_ret_1h_threshold: float = -0.005
    # Exposure
    max_weight: float = 0.30
    min_confidence: float = 0.60
    # Signal
    trending_strength_threshold: float = 1.0

    # Internal state
    _positions: dict[str, PositionState] = field(default_factory=dict)
    _last_trade_ts: dict[str, str] = field(default_factory=dict)
    _daily_trade_count: int = 0
    _current_day: str = ""

    def on_bar(self, bar: BarData) -> dict[str, float]:
        features = bar.features
        day = bar.ts[:10]

        # Reset daily trade count
        if day != self._current_day:
            self._current_day = day
            self._daily_trade_count = 0

        # Check existing position for profit target / stop loss / max age
        if bar.symbol in self._positions:
            pos = self._positions[bar.symbol]
            pnl_bps = (bar.mid_px - pos.entry_px) / pos.entry_px * 10000
            age_min = (_parse_ts(bar.ts) - _parse_ts(pos.entry_ts)).total_seconds() / 60

            # Profit target
            if pnl_bps >= self.profit_target_bps:
                del self._positions[bar.symbol]
                return {bar.symbol: 0.0}

            # Stop loss
            if pnl_bps <= -self.stop_loss_bps:
                del self._positions[bar.symbol]
                return {bar.symbol: 0.0}

            # Position max age (close if not profitable)
            if age_min >= self.position_max_age_min and pnl_bps <= 0:
                del self._positions[bar.symbol]
                return {bar.symbol: 0.0}

        # Check trade gating
        if self._daily_trade_count >= self.max_trades_per_day:
            return {bar.symbol: self._positions.get(bar.symbol, PositionState(bar.symbol, 0, bar.ts, 0)).weight}

        if bar.symbol in self._last_trade_ts:
            elapsed = (_parse_ts(bar.ts) - _parse_ts(self._last_trade_ts[bar.symbol])).total_seconds() / 60
            if elapsed < self.min_trade_interval_min:
                return {bar.symbol: self._positions.get(bar.symbol, PositionState(bar.symbol, 0, bar.ts, 0)).weight}

        # Determine direction from features
        trend_agree = features.get("trend_agree", 0)
        trend_strength = features.get("trend_strength_15m", 0)
        ret_1h = features.get("ret_1h", 0)

        # Bearish regime check
        is_bearish = ret_1h < self.bearish_ret_1h_threshold and features.get("trend_1h", 0) < 0

        # Generate signal
        weight = 0.0
        if trend_agree != 0 and abs(trend_strength) >= self.trending_strength_threshold:
            if trend_agree > 0:
                if is_bearish and self.bearish_regime_long_block:
                    weight = 0.0  # Block longs in bearish regime
                else:
                    weight = self.max_weight
            else:
                weight = -self.max_weight

        # Record trade if weight changed
        current_weight = self._positions.get(bar.symbol, PositionState(bar.symbol, 0, bar.ts, 0)).weight
        if abs(weight - current_weight) > 0.01:
            self._daily_trade_count += 1
            self._last_trade_ts[bar.symbol] = bar.ts
            if abs(weight) > 0.01:
                self._positions[bar.symbol] = PositionState(
                    symbol=bar.symbol, entry_px=bar.mid_px,
                    entry_ts=bar.ts, weight=weight,
                )
            elif bar.symbol in self._positions:
                del self._positions[bar.symbol]

        return {bar.symbol: weight}
