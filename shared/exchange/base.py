# shared/exchange/base.py
"""Abstract base class for exchange adapters.

All exchange adapters (Hyperliquid, OKX, Binance, etc.) must implement
this interface. The service layer depends only on this abstraction.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from shared.exchange.models import (
    NormalizedAccountState,
    NormalizedOrderResult,
    InstrumentSpec,
)


class ExchangeError(Exception):
    """Base class for all adapter-internal exceptions."""


class OrderRejectedError(ExchangeError):
    """Order was rejected by the exchange."""


class RateLimitError(ExchangeError):
    """Exchange rate limit triggered."""


class InstrumentNotFoundError(ExchangeError):
    """Instrument specification not found."""


class ExchangeAdapter(ABC):
    """Unified exchange interface.

    All exchange adapters implement this class. The service layer only
    depends on this interface.
    """

    # ── Market Data ──────────────────────────────────────────────────────────

    @abstractmethod
    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """Return {symbol: mid_price}."""

    @abstractmethod
    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """Return {"bids": [[px, sz], ...], "asks": [[px, sz], ...]}."""

    @abstractmethod
    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Return recent trades, each with price/size/side/time."""

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate (decimal, e.g., 0.0001)."""

    @abstractmethod
    def get_open_interest(self, symbol: str) -> float:
        """Return open interest (USD notional)."""

    # ── Order Management ─────────────────────────────────────────────────────

    @abstractmethod
    def place_limit(
        self,
        symbol: str,
        is_buy: bool,
        qty: float,
        limit_px: float,
        tif: str = "GTC",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> NormalizedOrderResult:
        """Place limit order. Return normalized result.

        Internal exceptions are converted to ExchangeError subclasses.
        """

    @abstractmethod
    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Cancel order. Return True on success.

        Returns True if order already filled/does not exist.
        """

    @abstractmethod
    def get_order_status(
        self, symbol: str, exchange_order_id: str
    ) -> NormalizedOrderResult:
        """Query order status."""

    # ── Account ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_account_state(self) -> NormalizedAccountState:
        """Return normalized account state (equity, positions, open orders)."""

    # ── Instrument Specification ─────────────────────────────────────────────

    @abstractmethod
    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Return instrument spec (min qty, price tick, fees, etc.)."""