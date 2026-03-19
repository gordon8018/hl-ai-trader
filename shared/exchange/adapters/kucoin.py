# shared/exchange/adapters/kucoin.py
"""KuCoin exchange adapter.

Implements ExchangeAdapter interface for KuCoin USDT-margined perpetual futures.
SDK: python-kucoin
Product type: USDT-M (USDT-margined perpetual)
Symbol mapping: {"BTC": "XBTUSDTM", "ETH": "ETHUSDTM", ...}
"""
from __future__ import annotations

import os
import time
import logging
from typing import Optional, List

from shared.exchange.base import ExchangeAdapter, ExchangeError, OrderRejectedError
from shared.exchange.models import (
    NormalizedOrderResult,
    NormalizedAccountState,
    NormalizedPosition,
    NormalizedOpenOrder,
    InstrumentSpec,
)

logger = logging.getLogger(__name__)

# Symbol mapping: internal symbol -> KuCoin instrument ID
# Note: KuCoin uses XBT instead of BTC, and USDTM suffix for USDT-margined
SYMBOL_MAP = {
    "BTC": "XBTUSDTM",
    "ETH": "ETHUSDTM",
    "SOL": "SOLUSDTM",
    "ADA": "ADAUSDTM",
    "DOGE": "DOGEUSDTM",
    "XRP": "XRPUSDTM",
    "AVAX": "AVAXUSDTM",
    "LINK": "LINKUSDTM",
    "MATIC": "MATICUSDTM",
    "ARB": "ARBUSDTM",
}

# Reverse mapping: KuCoin instrument ID -> internal symbol
INSTRUMENT_TO_SYMBOL = {v: k for k, v in SYMBOL_MAP.items()}


class KucoinAdapter(ExchangeAdapter):
    """KuCoin USDT-margined perpetual futures adapter.

    Uses python-kucoin SDK for all operations.
    Supports DRY_RUN mode for testing without real API calls.

    Note: DRY_RUN is read from os.environ on each method call, not at module import time,
    ensuring monkeypatch.setenv works in tests.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = "https://api-futures.kucoin.com",
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._base_url = base_url.rstrip("/")

        # Initialize SDK client (lazy for DRY_RUN)
        self._client = None

        # Cache for instrument specs and contracts list
        self._instrument_cache: dict[str, InstrumentSpec] = {}
        self._contracts_cache: Optional[list[dict]] = None
        self._contracts_cache_time: float = 0.0

        if not self._is_dry_run():
            self._init_sdk_client()

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    def _init_sdk_client(self):
        """Initialize KuCoin SDK client."""
        try:
            from kucoin.client import Client
            self._client = Client(
                key=self._api_key,
                secret=self._api_secret,
                passphrase=self._passphrase,
                url=self._base_url,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to init KuCoin SDK: {e}") from e

    def _get_instrument_id(self, symbol: str) -> str:
        """Convert internal symbol to KuCoin instrument ID."""
        return SYMBOL_MAP.get(symbol, symbol)

    def _get_internal_symbol(self, instrument_id: str) -> str:
        """Convert KuCoin instrument ID to internal symbol."""
        return INSTRUMENT_TO_SYMBOL.get(instrument_id, instrument_id)

    def _get_contracts(self, max_age_seconds: float = 300.0) -> list[dict]:
        """Get contracts list with caching.

        Args:
            max_age_seconds: Cache TTL in seconds (default 5 minutes)

        Returns:
            List of contract dicts from futures_get_symbols
        """
        now = time.monotonic()
        if self._contracts_cache is not None and (now - self._contracts_cache_time) < max_age_seconds:
            return self._contracts_cache

        contracts = self._client.futures_get_symbols()
        self._contracts_cache = contracts.get("data", [])
        self._contracts_cache_time = now
        return self._contracts_cache

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """Return mid prices for given symbols."""
        result = {}
        for symbol in symbols:
            instrument_id = self._get_instrument_id(symbol)

            if self._is_dry_run():
                result[symbol] = 50000.0 if symbol == "BTC" else 3000.0
                continue

            try:
                ticker = self._client.futures_get_ticker(instrument_id)
                data = ticker.get("data", {})
                if data:
                    bid = float(data.get("bestBidPrice", 0))
                    ask = float(data.get("bestAskPrice", 0))
                    if bid > 0 and ask > 0:
                        result[symbol] = (bid + ask) / 2
            except Exception as e:
                logger.warning(f"Failed to get mid for {symbol}: {e}")

        return result

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """Return order book with bids/asks."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return {
                "bids": [[50000.0 - i * 10, 1.0] for i in range(depth)],
                "asks": [[50010.0 + i * 10, 1.0] for i in range(depth)],
            }

        try:
            ob = self._client.futures_get_order_book(instrument_id)
            data = ob.get("data", {})

            bids = []
            for b in data.get("bids", [])[:depth]:
                bids.append([float(b[0]), float(b[1])])

            asks = []
            for a in data.get("asks", [])[:depth]:
                asks.append([float(a[0]), float(a[1])])

            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.warning(f"Failed to get L2 book for {symbol}: {e}")
            return {"bids": [], "asks": []}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Return recent trades."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return [
                {"price": 50000.0, "size": 0.1, "side": "buy", "time": time.time() * 1000}
                for _ in range(min(limit, 5))
            ]

        try:
            trades_resp = self._client.futures_get_trade_histories(instrument_id)
            trades = trades_resp.get("data", [])[:limit]

            result = []
            for t in trades:
                result.append({
                    "price": float(t.get("price", 0)),
                    "size": float(t.get("size", 0)),
                    "side": "buy" if t.get("side") == "buy" else "sell",
                    "time": int(t.get("time", 0)),
                })
            return result
        except Exception as e:
            logger.warning(f"Failed to get recent trades for {symbol}: {e}")
            return []

    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return 0.0001

        try:
            contracts = self._get_contracts()

            for c in contracts:
                if c.get("symbol") == instrument_id:
                    return float(c.get("fundingFeeRate", 0))

            return 0.0
        except Exception as e:
            logger.warning(f"Failed to get funding rate for {symbol}: {e}")
            return 0.0

    def get_open_interest(self, symbol: str) -> float:
        """Return open interest in USD."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return 1000000.0

        try:
            contracts = self._get_contracts()

            for c in contracts:
                if c.get("symbol") == instrument_id:
                    oi_contracts = float(c.get("openInterest", 0))
                    mark_px = float(c.get("markPrice", 0))
                    contract_size = float(c.get("multiplier", 1))
                    return oi_contracts * contract_size * mark_px

            return 0.0
        except Exception as e:
            logger.warning(f"Failed to get open interest for {symbol}: {e}")
            return 0.0

    # ── Order Management ─────────────────────────────────────────────────────

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
        """Place limit order."""
        t0 = time.monotonic()
        instrument_id = self._get_instrument_id(symbol)
        cid = client_order_id or f"kc_{int(time.time() * 1000)}"

        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id=cid,
                exchange_order_id=f"dry_kc_{cid}",
                status="ack",
                filled_qty=0.0,
                avg_px=0.0,
                fee_usd=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        spec = self.get_instrument_spec(symbol)

        tif_map = {"GTC": "GTC", "IOC": "IOC", "FOK": "FOK", "GTX": "PO"}
        kucoin_tif = tif_map.get(tif.upper(), "GTC")

        try:
            side = "buy" if is_buy else "sell"

            # Convert qty (in base currency) to contracts
            # KuCoin size = number of contracts = qty / contract_size
            contracts = int(qty / spec.contract_size) if spec.contract_size > 0 else 0
            if contracts <= 0:
                raise OrderRejectedError(f"Order quantity {qty} too small (min: {spec.contract_size})")

            result = self._client.futures_create_order(
                symbol=instrument_id,
                side=side,
                lever="1",
                size=str(contracts),
                price=str(limit_px),
                time_in_force=kucoin_tif,
                clientOid=cid,
                reduceOnly=reduce_only,
            )

            data = result.get("data", {})
            order_id = data.get("orderId", "")

            return NormalizedOrderResult(
                client_order_id=cid,
                exchange_order_id=order_id,
                status="ack",
                filled_qty=0.0,
                avg_px=0.0,
                fee_usd=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            raise OrderRejectedError(f"KuCoin order rejected: {e}") from e

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Cancel order."""
        if self._is_dry_run():
            return True

        try:
            result = self._client.futures_cancel_order(exchange_order_id)
            # KuCoin success: code == "200000" and data exists
            code = result.get("code", "")
            if code == "200000":
                return True
            # Also check if cancelledOrderId is in response (alternative success indicator)
            if result.get("data", {}).get("cancelledOrderId"):
                return True
            logger.warning(f"Unexpected cancel response: {result}")
            return False
        except Exception as e:
            logger.warning(f"Failed to cancel order {exchange_order_id}: {e}")
            return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        """Get order status."""
        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id="",
                exchange_order_id=exchange_order_id,
                status="ack",
                filled_qty=0.0,
                avg_px=0.0,
                fee_usd=0.0,
                latency_ms=0,
            )

        try:
            result = self._client.futures_get_order(exchange_order_id)
            data = result.get("data", {})

            status_map = {
                "open": "ack",
                "active": "ack",
                "done": "filled",
                "filled": "filled",
                "canceled": "canceled",
                "cancelled": "canceled",
            }
            raw_status = data.get("status", "open").lower()
            status = status_map.get(raw_status, "ack")

            filled_qty = float(data.get("dealSize", 0))
            avg_px = 0.0
            if filled_qty > 0:
                avg_px = float(data.get("dealValue", 0)) / filled_qty

            return NormalizedOrderResult(
                client_order_id=data.get("clientOid", ""),
                exchange_order_id=exchange_order_id,
                status=status,
                filled_qty=filled_qty,
                avg_px=avg_px,
                fee_usd=float(data.get("fee", 0)),
                latency_ms=0,
            )
        except Exception as e:
            raise ExchangeError(f"Failed to get order status: {e}") from e

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_state(self) -> NormalizedAccountState:
        """Return account state with positions and open orders."""
        if self._is_dry_run():
            return NormalizedAccountState(
                equity_usd=10000.0,
                cash_usd=8000.0,
                positions={},
                open_orders=[],
            )

        try:
            overview = self._client.futures_get_account_detail("USDT")
            data = overview.get("data", {})

            equity = float(data.get("accountEquity", 0))
            cash = float(data.get("availableBalance", 0))

            positions = {}
            pos_resp = self._client.futures_get_positions()
            for pos in pos_resp.get("data", []):
                symbol = self._get_internal_symbol(pos.get("symbol", ""))
                qty = float(pos.get("currentQty", 0))

                if qty != 0:
                    positions[symbol] = NormalizedPosition(
                        symbol=symbol,
                        qty=qty,
                        entry_px=float(pos.get("avgEntryPrice", 0)),
                        mark_px=float(pos.get("markPrice", 0)),
                        unreal_pnl=float(pos.get("unrealisedPnl", 0)),
                        side="long" if qty > 0 else "short",
                    )

            open_orders = []
            # Only fetch active orders (status=open)
            orders_resp = self._client.futures_get_orders(status="open")
            for o in orders_resp.get("data", []):
                symbol = self._get_internal_symbol(o.get("symbol", ""))
                open_orders.append(NormalizedOpenOrder(
                    exchange_order_id=str(o.get("id", "")),
                    client_order_id=o.get("clientOid", ""),
                    symbol=symbol,
                    side="buy" if o.get("side") == "buy" else "sell",
                    qty=float(o.get("size", 0)),
                    limit_px=float(o.get("price", 0)),
                    filled_qty=float(o.get("dealSize", 0)),
                ))

            return NormalizedAccountState(
                equity_usd=equity,
                cash_usd=cash,
                positions=positions,
                open_orders=open_orders,
            )
        except Exception as e:
            raise ExchangeError(f"Failed to get account state: {e}") from e

    # ── Instrument Specification ─────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Return instrument specification."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return InstrumentSpec(
                symbol=symbol,
                min_qty=0.001,
                qty_step=0.001,
                price_tick=0.1,
                contract_size=0.001,
                maker_fee=0.0002,
                taker_fee=0.0006,
            )

        try:
            contracts = self._get_contracts()

            for c in contracts:
                if c.get("symbol") == instrument_id:
                    spec = InstrumentSpec(
                        symbol=symbol,
                        min_qty=float(c.get("lotSize", 0.001)),
                        qty_step=float(c.get("lotSize", 0.001)),
                        price_tick=float(c.get("tickSize", 0.1)),
                        contract_size=float(c.get("multiplier", 1)),
                        maker_fee=float(c.get("makerFeeRate", 0.0002)),
                        taker_fee=float(c.get("takerFeeRate", 0.0006)),
                    )
                    self._instrument_cache[symbol] = spec
                    return spec

            # Symbol not found - this is a configuration error
            raise ValueError(f"Symbol {symbol} not found in KuCoin contracts")
        except ValueError:
            # Re-raise ValueError (symbol not found)
            raise
        except Exception as e:
            logger.warning(f"Failed to get instrument spec for {symbol}: {e}")
            return InstrumentSpec(
                symbol=symbol,
                min_qty=0.001,
                qty_step=0.001,
                price_tick=0.1,
                contract_size=0.001,
                maker_fee=0.0002,
                taker_fee=0.0006,
            )