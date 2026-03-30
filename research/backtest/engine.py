"""Event-driven backtesting engine for crypto perpetual futures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

import polars as pl

from research.backtest.cost_model import CryptoPerpCostModel


@dataclass
class BarData:
    ts: str
    symbol: str
    mid_px: float
    top_depth_usd: float = 500000.0
    funding_rate: float = 0.0
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestResult:
    equity_curve: list[float]
    trades: pl.DataFrame
    metrics: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    def on_bar(self, bar: BarData) -> dict[str, float]:
        """Return target weights per symbol. E.g. {'BTC': 0.15, 'ETH': -0.05}"""
        ...


class BacktestEngine:
    """Simple event-driven backtest: iterates bars, diffs positions, applies costs."""

    def __init__(self, cost_model: CryptoPerpCostModel | None = None):
        self.cost_model = cost_model or CryptoPerpCostModel()

    def run(
        self,
        strategy: Strategy,
        bars: Sequence[BarData],
        initial_capital: float = 10000.0,
    ) -> BacktestResult:
        equity = initial_capital
        # positions stored as qty in asset units
        pos_qty: dict[str, float] = {}    # symbol → qty in asset units
        prev_prices: dict[str, float] = {}  # symbol → last known price
        equity_curve: list[float] = []
        trade_records: list[dict[str, Any]] = []

        for bar in bars:
            # 1. Mark-to-market BEFORE updating price
            if bar.symbol in pos_qty and bar.symbol in prev_prices:
                qty = pos_qty[bar.symbol]
                price_change = bar.mid_px - prev_prices[bar.symbol]
                pnl = qty * price_change  # qty can be negative (short)
                equity += pnl

            # 2. Update price
            prev_prices[bar.symbol] = bar.mid_px

            # 3. Get target weights from strategy
            target_weights = strategy.on_bar(bar)

            # 4. Convert weights to target qty and execute trades
            for symbol, weight in target_weights.items():
                px = prev_prices.get(symbol, bar.mid_px)
                if px <= 0:
                    continue
                target_notional = equity * weight
                target_qty = target_notional / px
                current_qty = pos_qty.get(symbol, 0.0)
                trade_qty = target_qty - current_qty
                trade_notional = abs(trade_qty * px)

                if trade_notional < 1.0:  # skip tiny trades
                    continue

                # Apply trade cost
                cost = self.cost_model.trade_cost(trade_notional, bar)
                equity -= cost

                trade_records.append({
                    "ts": bar.ts,
                    "symbol": symbol,
                    "side": "BUY" if trade_qty > 0 else "SELL",
                    "notional": trade_notional,
                    "price": px,
                    "cost": cost,
                    "pnl": 0.0,
                })

                pos_qty[symbol] = target_qty

            # 5. Apply funding cost on all held positions
            for symbol, qty in pos_qty.items():
                notional = abs(qty * prev_prices.get(symbol, 0))
                if notional > 0:
                    funding = self.cost_model.funding_cost(notional, bar)
                    equity -= funding

            equity_curve.append(equity)

        # Compute trade PnL (simplified: assign proportional PnL)
        trades_df = pl.DataFrame(trade_records) if trade_records else pl.DataFrame({
            "ts": [], "symbol": [], "side": [], "notional": [],
            "price": [], "cost": [], "pnl": [],
        })

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades_df,
        )
