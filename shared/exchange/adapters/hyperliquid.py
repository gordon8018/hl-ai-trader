# shared/exchange/adapters/hyperliquid.py
"""Hyperliquid exchange adapter (stub for Task 5 implementation)."""
from shared.exchange.base import ExchangeAdapter
from shared.exchange.models import (
    NormalizedAccountState,
    NormalizedOrderResult,
    InstrumentSpec,
)


class HyperliquidAdapter(ExchangeAdapter):
    """Stub adapter for Hyperliquid exchange. Full implementation in Task 5."""

    def __init__(self, private_key: str, account_address: str, base_url: str = "https://api.hyperliquid.xyz"):
        self.private_key = private_key
        self.account_address = account_address
        self.base_url = base_url

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        raise NotImplementedError("HyperliquidAdapter.get_all_mids not implemented")

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        raise NotImplementedError("HyperliquidAdapter.get_l2_book not implemented")

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError("HyperliquidAdapter.get_recent_trades not implemented")

    def get_funding_rate(self, symbol: str) -> float:
        raise NotImplementedError("HyperliquidAdapter.get_funding_rate not implemented")

    def get_open_interest(self, symbol: str) -> float:
        raise NotImplementedError("HyperliquidAdapter.get_open_interest not implemented")

    def place_limit(
        self,
        symbol: str,
        is_buy: bool,
        qty: float,
        limit_px: float,
        tif: str = "GTC",
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> NormalizedOrderResult:
        raise NotImplementedError("HyperliquidAdapter.place_limit not implemented")

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        raise NotImplementedError("HyperliquidAdapter.cancel_order not implemented")

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        raise NotImplementedError("HyperliquidAdapter.get_order_status not implemented")

    def get_account_state(self) -> NormalizedAccountState:
        raise NotImplementedError("HyperliquidAdapter.get_account_state not implemented")

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        raise NotImplementedError("HyperliquidAdapter.get_instrument_spec not implemented")