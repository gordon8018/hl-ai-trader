# shared/hl/client.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Any, Dict

@dataclass
class HLConfig:
    private_key: str
    account_address: str
    base_url: str

class HyperliquidClient:
    """
    Wrapper around hyperliquid-python-sdk.
    We intentionally keep a very small surface area.

    NOTE: SDK API can change; if you see runtime errors, adjust the wrapper
    to match your installed SDK version.
    """
    def __init__(self, cfg: HLConfig):
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants
        except Exception as e:
            raise RuntimeError(
                "Missing dependencies for Hyperliquid trading. "
                "Install: pip install hyperliquid-python-sdk eth-account"
            ) from e

        self._Account = Account
        self._constants = constants

        # Build wallet
        wallet = Account.from_key(cfg.private_key)

        # base_url: prefer cfg.base_url if provided; else use constants.MAINNET_API_URL
        base_url = cfg.base_url or getattr(constants, "MAINNET_API_URL", "https://api.hyperliquid.xyz")

        # Exchange signature (per docs/examples): Exchange(wallet, base_url, account_address=address)
        # account_address should be the MAIN account (not API wallet) if using an API wallet. :contentReference[oaicite:3]{index=3}
        self.exchange = Exchange(wallet, base_url, account_address=cfg.account_address)

    def place_limit(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        limit_px: float,
        tif: str = "Gtc",
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Typical SDK signature is exchange.order(coin, is_buy, sz, limit_px, order_type, reduce_only=...)
        If your SDK differs, adapt here.
        """
        order_type = {"limit": {"tif": tif}}
        # Most SDK versions: exchange.order(coin, is_buy, sz, limit_px, order_type, reduce_only=reduce_only)
        return self.exchange.order(coin, is_buy, sz, limit_px, order_type, reduce_only=reduce_only) # type: ignore

    def cancel(self, coin: str, oid: int) -> Dict[str, Any]:
        # Most SDK versions: exchange.cancel(coin, oid)
        return self.exchange.cancel(coin, oid)
