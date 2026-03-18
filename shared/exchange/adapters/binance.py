# shared/exchange/adapters/binance.py
"""Binance exchange adapter.

Implements ExchangeAdapter interface for Binance USDT-M Futures.
SDK: binance-futures-connector
Endpoint: FAPI (https://fapi.binance.com)
Symbol mapping: {"BTC": "BTCUSDT", "ETH": "ETHUSDT", ...}
Hedge mode required for simultaneous long/short positions.
"""
from __future__ import annotations

import os
import time
import logging
from typing import Optional

from shared.exchange.base import ExchangeAdapter, ExchangeError, OrderRejectedError
from shared.exchange.models import (
    NormalizedOrderResult,
    NormalizedAccountState,
    NormalizedPosition,
    NormalizedOpenOrder,
    InstrumentSpec,
)

logger = logging.getLogger(__name__)

# Symbol mapping: internal symbol -> Binance symbol
SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "XRP": "XRPUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "MATIC": "MATICUSDT",
    "ARB": "ARBUSDT",
}

# Reverse mapping: Binance symbol -> internal symbol
INSTRUMENT_TO_SYMBOL = {v: k for k, v in SYMBOL_MAP.items()}


class BinanceAdapter(ExchangeAdapter):
    """Binance USDT-M Futures adapter.

    Uses binance-futures-connector SDK for all operations.
    Supports DRY_RUN mode for testing without real API calls.
    Requires hedge mode for simultaneous long/short positions.

    Note: DRY_RUN is read from os.environ on each method call, not at module import time,
    ensuring monkeypatch.setenv works in tests.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.binance.com",
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")

        # Initialize SDK client (lazy for DRY_RUN)
        self._client = None

        # Cache for instrument specs
        self._instrument_cache: dict[str, InstrumentSpec] = {}

        if not self._is_dry_run():
            self._init_sdk_client()

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    def _init_sdk_client(self):
        """Initialize Binance SDK client."""
        try:
            from binance.um_futures import UMFutures

            self._client = UMFutures(
                key=self._api_key,
                secret=self._api_secret,
                base_url=self._base_url,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to init Binance SDK: {e}") from e

    def _get_symbol(self, internal_symbol: str) -> str:
        """Convert internal symbol to Binance symbol."""
        if internal_symbol in SYMBOL_MAP:
            return SYMBOL_MAP[internal_symbol]
        # If not in map, assume it's already a Binance symbol
        return internal_symbol

    def _get_internal_symbol(self, binance_symbol: str) -> str:
        """Convert Binance symbol to internal symbol."""
        if binance_symbol in INSTRUMENT_TO_SYMBOL:
            return INSTRUMENT_TO_SYMBOL[binance_symbol]
        return binance_symbol

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """Return {symbol: mid_price} for the given symbols. Single API call."""
        if self._is_dry_run() or self._client is None:
            return {sym: 50000.0 for sym in symbols}

        result: dict[str, float] = {}
        try:
            tickers = self._client.book_ticker()  # fetch all symbols at once
            for item in tickers:
                binance_sym = item.get("symbol", "")
                internal_sym = INSTRUMENT_TO_SYMBOL.get(binance_sym)
                if internal_sym and internal_sym in symbols:
                    bid = float(item.get("bidPrice", 0) or 0)
                    ask = float(item.get("askPrice", 0) or 0)
                    if bid > 0 and ask > 0:
                        result[internal_sym] = (bid + ask) / 2
        except Exception as e:
            logger.warning(f"Failed to get all mids: {e}")
        return result

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """Return {"bids": [[px, sz], ...], "asks": [[px, sz], ...]}."""
        if self._is_dry_run() or self._client is None:
            return {
                "bids": [["50000.0", "1.0"]] * depth,
                "asks": [["50010.0", "1.0"]] * depth,
            }

        binance_symbol = self._get_symbol(symbol)
        try:
            book = self._client.depth(symbol=binance_symbol, limit=str(depth))
            bids = [[b[0], b[1]] for b in book.get("bids", [])[:depth]]
            asks = [[a[0], a[1]] for a in book.get("asks", [])[:depth]]
            return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.warning(f"Failed to get L2 book for {symbol}: {e}")
        return {"bids": [], "asks": []}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Return recent trades, each with price/size/side/time."""
        if self._is_dry_run() or self._client is None:
            return []

        binance_symbol = self._get_symbol(symbol)
        try:
            trades_raw = self._client.recent_trades(symbol=binance_symbol, limit=str(limit))
            trades = []
            for t in trades_raw:
                trades.append({
                    "price": float(t.get("price", 0)),
                    "size": float(t.get("qty", 0)),
                    "side": "buy" if t.get("isBuyerMaker", False) is False else "sell",
                    "time": t.get("time", ""),
                })
            return trades
        except Exception as e:
            logger.warning(f"Failed to get recent trades for {symbol}: {e}")
        return []

    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate (decimal, e.g., 0.0001)."""
        if self._is_dry_run() or self._client is None:
            return 0.0001

        binance_symbol = self._get_symbol(symbol)
        try:
            premium = self._client.premium_index(symbol=binance_symbol)
            if isinstance(premium, list) and premium:
                return float(premium[0].get("lastFundingRate", 0))
            elif isinstance(premium, dict):
                return float(premium.get("lastFundingRate", 0))
        except Exception as e:
            logger.warning(f"Failed to get funding rate for {symbol}: {e}")
        return 0.0

    def get_open_interest(self, symbol: str) -> float:
        """Return open interest (USD notional)."""
        if self._is_dry_run() or self._client is None:
            return 1000000.0

        binance_symbol = self._get_symbol(symbol)
        try:
            oi_data = self._client.open_interest(symbol=binance_symbol)
            return float(oi_data.get("openInterest", 0))
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
        """Place limit order. Return normalized result.

        Binance hedge mode requires positionSide (LONG/SHORT) for orders.
        """
        t0 = time.monotonic()
        binance_symbol = self._get_symbol(symbol)
        cid = client_order_id or f"bn_{int(time.time()*1000)}"

        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id=cid,
                exchange_order_id=f"dry_{cid}",
                status="ack",
                filled_qty=0.0,
                avg_px=0.0,
                fee_usd=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        # One-way mode: positionSide=BOTH matches Hyperliquid's single-direction behavior.
        # reduce_only controls whether this closes an existing position.
        position_side = "BOTH"
        side = "BUY" if is_buy else "SELL"

        # TIF mapping: GTC, IOC, FOK, GTX (Post Only)
        time_in_force = tif.upper() if tif.upper() in ["GTC", "IOC", "FOK", "GTX"] else "GTC"

        # Format quantity precision
        spec = self.get_instrument_spec(symbol)
        qty_str = self._format_qty(qty, spec)

        params = {
            "symbol": binance_symbol,
            "side": side,
            "positionSide": position_side,
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": qty_str,
            "price": str(limit_px),
            "newClientOrderId": cid,
        }

        if reduce_only:
            params["reduceOnly"] = "true"

        try:
            resp = self._client.new_order(**params)

            if resp:
                order_id = str(resp.get("orderId", ""))
                status_raw = resp.get("status", "NEW")
                # Binance status: NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED, REJECTED
                status_map = {
                    "NEW": "ack",
                    "PARTIALLY_FILLED": "ack",
                    "FILLED": "filled",
                    "CANCELED": "canceled",
                    "EXPIRED": "canceled",
                    "REJECTED": "rejected",
                }

                return NormalizedOrderResult(
                    client_order_id=cid,
                    exchange_order_id=order_id,
                    status=status_map.get(status_raw, "ack"),
                    filled_qty=float(resp.get("executedQty", 0)),
                    avg_px=float(resp.get("avgPrice", 0) or 0),
                    fee_usd=0.0,  # Fee not returned in order response
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )

        except Exception as e:
            error_msg = str(e)
            if "code" in error_msg or "Insufficient" in error_msg:
                raise OrderRejectedError(f"Binance order rejected: {e}") from e
            raise OrderRejectedError(f"Binance order failed: {e}") from e

        raise OrderRejectedError("Binance order failed: no response")

    def _format_qty(self, qty: float, spec: InstrumentSpec) -> str:
        """Format quantity according to instrument precision."""
        # Round to step size
        if spec.qty_step > 0:
            qty = round(qty / spec.qty_step) * spec.qty_step
        return f"{qty:.8f}".rstrip("0").rstrip(".")

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Cancel order. Return True on success."""
        if self._is_dry_run():
            return True

        binance_symbol = self._get_symbol(symbol)
        try:
            resp = self._client.cancel_order(
                symbol=binance_symbol,
                orderId=exchange_order_id,
            )
            # Response contains status, order is cancelled if we get here
            return True
        except Exception as e:
            # Order might already be filled or cancelled
            error_msg = str(e)
            if "Unknown order" in error_msg or "does not exist" in error_msg:
                return True
            logger.warning(f"Failed to cancel order {exchange_order_id}: {e}")
        return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        """Query order status."""
        binance_symbol = self._get_symbol(symbol)
        cid = f"query_{exchange_order_id}"

        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id=cid,
                exchange_order_id=exchange_order_id,
                status="ack",
                filled_qty=0.0,
                avg_px=0.0,
                fee_usd=0.0,
                latency_ms=0,
            )

        try:
            resp = self._client.query_order(
                symbol=binance_symbol,
                orderId=exchange_order_id,
            )

            if resp:
                status_raw = resp.get("status", "NEW")
                status_map = {
                    "NEW": "ack",
                    "PARTIALLY_FILLED": "ack",
                    "FILLED": "filled",
                    "CANCELED": "canceled",
                    "EXPIRED": "canceled",
                    "REJECTED": "rejected",
                }

                return NormalizedOrderResult(
                    client_order_id=resp.get("clientOrderId", ""),
                    exchange_order_id=str(resp.get("orderId", "")),
                    status=status_map.get(status_raw, "ack"),
                    filled_qty=float(resp.get("executedQty", 0)),
                    avg_px=float(resp.get("avgPrice", 0) or 0),
                    fee_usd=0.0,
                    latency_ms=0,
                )

        except Exception as e:
            logger.warning(f"Failed to get order status for {exchange_order_id}: {e}")

        return NormalizedOrderResult(
            client_order_id=cid,
            exchange_order_id=exchange_order_id,
            status="ack",
            filled_qty=0.0,
            avg_px=0.0,
            fee_usd=0.0,
            latency_ms=0,
        )

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_state(self) -> NormalizedAccountState:
        """Return normalized account state (equity, positions, open orders)."""
        if self._is_dry_run() or self._client is None:
            return NormalizedAccountState(
                equity_usd=10000.0,
                cash_usd=9000.0,
                positions={},
                open_orders=[],
            )

        try:
            # Get account balance
            account = self._client.account()
            equity = 0.0
            cash = 0.0

            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    equity = float(asset.get("walletBalance", 0))
                    cash = float(asset.get("availableBalance", 0))
                    break

            # Get positions
            positions = {}
            for pos in account.get("positions", []):
                pos_qty = float(pos.get("positionAmt", 0))
                if pos_qty == 0:
                    continue

                binance_symbol = pos.get("symbol", "")
                internal_symbol = self._get_internal_symbol(binance_symbol)

                positions[internal_symbol] = NormalizedPosition(
                    symbol=internal_symbol,
                    qty=abs(pos_qty) * (1 if pos.get("positionSide") == "LONG" else -1),
                    entry_px=float(pos.get("entryPrice", 0)),
                    mark_px=float(pos.get("markPrice", 0)),
                    unreal_pnl=float(pos.get("unRealizedProfit", 0)),
                    side=pos.get("positionSide", "BOTH").lower(),
                )

            # Get open orders
            open_orders = []
            orders = self._client.get_open_orders()
            for order in orders:
                binance_symbol = order.get("symbol", "")
                internal_symbol = self._get_internal_symbol(binance_symbol)

                open_orders.append(
                    NormalizedOpenOrder(
                        exchange_order_id=str(order.get("orderId", "")),
                        client_order_id=order.get("clientOrderId", ""),
                        symbol=internal_symbol,
                        side=order.get("side", "").lower(),
                        qty=float(order.get("origQty", 0)),
                        limit_px=float(order.get("price", 0)),
                        filled_qty=float(order.get("executedQty", 0)),
                    )
                )

            return NormalizedAccountState(
                equity_usd=equity,
                cash_usd=cash,
                positions=positions,
                open_orders=open_orders,
            )

        except Exception as e:
            logger.error(f"Failed to get account state: {e}")
            raise ExchangeError(f"Failed to get account state: {e}") from e

    # ── Instrument Specification ─────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Return instrument spec (min qty, price tick, fees, etc.)."""
        # Check cache first
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        binance_symbol = self._get_symbol(symbol)

        if self._is_dry_run() or self._client is None:
            # Return default spec for DRY_RUN
            return InstrumentSpec(
                symbol=symbol,
                min_qty=0.001,
                qty_step=0.001,
                price_tick=0.1,
                contract_size=1.0,  # Binance uses 1.0 (not contract-based)
                maker_fee=0.0002,
                taker_fee=0.0004,
            )

        try:
            # Get exchange info
            exchange_info = self._client.exchange_info()
            for inst in exchange_info.get("symbols", []):
                if inst.get("symbol") == binance_symbol:
                    # Parse filters
                    min_qty = 0.001
                    qty_step = 0.001
                    price_tick = 0.1

                    for f in inst.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            min_qty = float(f.get("minQty", min_qty))
                            qty_step = float(f.get("stepSize", qty_step))
                        elif f.get("filterType") == "PRICE_FILTER":
                            price_tick = float(f.get("tickSize", price_tick))

                    spec = InstrumentSpec(
                        symbol=symbol,
                        min_qty=min_qty,
                        qty_step=qty_step,
                        price_tick=price_tick,
                        contract_size=1.0,  # Binance USDT-M uses 1.0
                        maker_fee=0.0002,  # Default maker fee
                        taker_fee=0.0004,  # Default taker fee
                    )
                    self._instrument_cache[symbol] = spec
                    return spec

        except Exception as e:
            logger.warning(f"Failed to get instrument spec for {symbol}: {e}")

        # Return default spec if API fails
        return InstrumentSpec(
            symbol=symbol,
            min_qty=0.001,
            qty_step=0.001,
            price_tick=0.1,
            contract_size=1.0,
            maker_fee=0.0002,
            taker_fee=0.0004,
        )