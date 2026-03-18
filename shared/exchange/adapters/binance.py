# shared/exchange/adapters/binance.py
"""Binance exchange adapter (stub for Task 14 implementation)."""
from shared.exchange.base import ExchangeAdapter
from shared.exchange.models import (
    NormalizedAccountState,
    NormalizedOrderResult,
    InstrumentSpec,
)


class BinanceAdapter(ExchangeAdapter):
    """Stub adapter for Binance exchange. Full implementation in Task 14."""

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://fapi.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        raise NotImplementedError("BinanceAdapter.get_all_mids not implemented")

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        raise NotImplementedError("BinanceAdapter.get_l2_book not implemented")

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError("BinanceAdapter.get_recent_trades not implemented")

    def get_funding_rate(self, symbol: str) -> float:
        raise NotImplementedError("BinanceAdapter.get_funding_rate not implemented")

    def get_open_interest(self, symbol: str) -> float:
        raise NotImplementedError("BinanceAdapter.get_open_interest not implemented")

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
        raise NotImplementedError("BinanceAdapter.place_limit not implemented")

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        raise NotImplementedError("BinanceAdapter.cancel_order not implemented")

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        raise NotImplementedError("BinanceAdapter.get_order_status not implemented")

    def get_account_state(self) -> NormalizedAccountState:
        raise NotImplementedError("BinanceAdapter.get_account_state not implemented")

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        raise NotImplementedError("BinanceAdapter.get_instrument_spec not implemented")