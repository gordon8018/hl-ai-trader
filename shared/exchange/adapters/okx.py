# shared/exchange/adapters/okx.py
"""OKX exchange adapter (stub for Task 11 implementation)."""
from shared.exchange.base import ExchangeAdapter
from shared.exchange.models import (
    NormalizedAccountState,
    NormalizedOrderResult,
    InstrumentSpec,
)


class OKXAdapter(ExchangeAdapter):
    """Stub adapter for OKX exchange. Full implementation in Task 11."""

    def __init__(self, api_key: str, api_secret: str, passphrase: str, base_url: str = "https://www.okx.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        raise NotImplementedError("OKXAdapter.get_all_mids not implemented")

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        raise NotImplementedError("OKXAdapter.get_l2_book not implemented")

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError("OKXAdapter.get_recent_trades not implemented")

    def get_funding_rate(self, symbol: str) -> float:
        raise NotImplementedError("OKXAdapter.get_funding_rate not implemented")

    def get_open_interest(self, symbol: str) -> float:
        raise NotImplementedError("OKXAdapter.get_open_interest not implemented")

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
        raise NotImplementedError("OKXAdapter.place_limit not implemented")

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        raise NotImplementedError("OKXAdapter.cancel_order not implemented")

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        raise NotImplementedError("OKXAdapter.get_order_status not implemented")

    def get_account_state(self) -> NormalizedAccountState:
        raise NotImplementedError("OKXAdapter.get_account_state not implemented")

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        raise NotImplementedError("OKXAdapter.get_instrument_spec not implemented")