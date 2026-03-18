# shared/exchange/factory.py
"""Factory function to create exchange adapters based on EXCHANGE env var."""
from __future__ import annotations
import os
from shared.exchange.base import ExchangeAdapter


def create_exchange_adapter() -> ExchangeAdapter:
    """Create an exchange adapter based on the EXCHANGE environment variable.

    Returns:
        An instance of the appropriate exchange adapter.

    Raises:
        ValueError: If EXCHANGE is set to an unsupported value.
        KeyError: If required environment variables are missing.
    """
    exchange = os.environ.get("EXCHANGE", "hyperliquid").lower()

    if exchange == "hyperliquid":
        from shared.exchange.adapters.hyperliquid import HyperliquidAdapter
        return HyperliquidAdapter(
            private_key=os.environ["HL_PRIVATE_KEY"],
            account_address=os.environ["HL_ACCOUNT_ADDRESS"],
            base_url=os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz"),
        )

    elif exchange == "okx":
        from shared.exchange.adapters.okx import OKXAdapter
        return OKXAdapter(
            api_key=os.environ["OKX_API_KEY"],
            api_secret=os.environ["OKX_API_SECRET"],
            passphrase=os.environ["OKX_PASSPHRASE"],
            base_url=os.environ.get("OKX_HTTP_URL", "https://www.okx.com"),
        )

    elif exchange == "binance":
        from shared.exchange.adapters.binance import BinanceAdapter
        return BinanceAdapter(
            api_key=os.environ["BINANCE_API_KEY"],
            api_secret=os.environ["BINANCE_API_SECRET"],
            base_url=os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com"),
        )

    else:
        raise ValueError(f"Unsupported exchange: {exchange!r}. Choose: hyperliquid, okx, binance")