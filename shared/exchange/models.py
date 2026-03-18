# shared/exchange/models.py
"""Normalized data models for multi-exchange support.

These models provide a unified interface for different exchanges
(Hyperliquid, OKX, Binance) to ensure consistent data handling.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NormalizedTicker:
    """Normalized ticker data.

    Attributes:
        symbol: Trading symbol (e.g., "BTC", "ETH")
        mid_px: Mid-price between bid and ask
        bid: Best bid price
        ask: Best ask price
        spread_bps: Spread in basis points
    """
    symbol: str
    mid_px: float
    bid: float
    ask: float
    spread_bps: float


@dataclass
class NormalizedPosition:
    """Normalized position data.

    Attributes:
        symbol: Trading symbol
        qty: Position quantity (positive=long, negative=short)
        entry_px: Average entry price
        mark_px: Current mark price
        unreal_pnl: Unrealized profit/loss
        side: Position side ("long" | "short" | "flat", lowercase per exchange API convention)
    """
    symbol: str
    qty: float          # positive=long, negative=short
    entry_px: float
    mark_px: float
    unreal_pnl: float
    side: str           # "long" | "short" | "flat" (lowercase per exchange API convention)


@dataclass
class NormalizedOpenOrder:
    """Normalized open order data.

    Attributes:
        exchange_order_id: Order ID assigned by the exchange
        client_order_id: Client-assigned order ID
        symbol: Trading symbol
        side: Order side ("buy" | "sell", lowercase per exchange API convention)
        qty: Order quantity
        limit_px: Limit price
        filled_qty: Quantity filled so far
    """
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: str           # "buy" | "sell" (lowercase per exchange API convention)
    qty: float
    limit_px: float
    filled_qty: float


@dataclass
class NormalizedAccountState:
    """Normalized account state.

    Attributes:
        equity_usd: Total account equity in USD
        cash_usd: Available cash in USD
        positions: Dict of symbol to NormalizedPosition
        open_orders: List of open orders
    """
    equity_usd: float
    cash_usd: float
    positions: dict[str, NormalizedPosition] = field(default_factory=dict)
    open_orders: list[NormalizedOpenOrder] = field(default_factory=list)


@dataclass
class NormalizedOrderResult:
    """Normalized order result.

    Attributes:
        client_order_id: Client-assigned order ID
        exchange_order_id: Order ID assigned by the exchange
        status: Order status ("ack" | "filled" | "rejected" | "canceled", lowercase per exchange API convention)
        filled_qty: Quantity filled
        avg_px: Average fill price
        fee_usd: Fee in USD
        latency_ms: Order latency in milliseconds
    """
    client_order_id: str
    exchange_order_id: str
    status: str         # "ack" | "filled" | "rejected" | "canceled" (lowercase per exchange API convention)
    filled_qty: float
    avg_px: float
    fee_usd: float
    latency_ms: int


@dataclass
class InstrumentSpec:
    """Instrument specification.

    Attributes:
        symbol: Trading symbol
        min_qty: Minimum order quantity
        qty_step: Quantity step size
        price_tick: Price tick size
        contract_size: Contract size (OKX uses contract value; Binance/HL use 1.0)
        maker_fee: Maker fee rate
        taker_fee: Taker fee rate
    """
    symbol: str
    min_qty: float
    qty_step: float
    price_tick: float
    contract_size: float  # OKX contract value; Binance/HL use 1.0
    maker_fee: float
    taker_fee: float