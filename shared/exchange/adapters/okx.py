# shared/exchange/adapters/okx.py
"""OKX exchange adapter.

Implements ExchangeAdapter interface for OKX perpetuals (SWAP).
SDK: python-okx
Product type: SWAP (perpetual contracts)
Symbol mapping: {"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP", ...}
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

# Symbol mapping: internal symbol -> OKX instrument ID
SYMBOL_MAP = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP",
    "ADA": "ADA-USDT-SWAP",
    "DOGE": "DOGE-USDT-SWAP",
    "XRP": "XRP-USDT-SWAP",
    "AVAX": "AVAX-USDT-SWAP",
    "LINK": "LINK-USDT-SWAP",
    "MATIC": "MATIC-USDT-SWAP",
    "ARB": "ARB-USDT-SWAP",
}

# Reverse mapping: OKX instrument ID -> internal symbol
INSTRUMENT_TO_SYMBOL = {v: k for k, v in SYMBOL_MAP.items()}


class OKXAdapter(ExchangeAdapter):
    """OKX perpetuals adapter.

    Uses python-okx SDK for all operations.
    Supports DRY_RUN mode for testing without real API calls.

    Note: DRY_RUN is read from os.environ on each method call, not at module import time,
    ensuring monkeypatch.setenv works in tests.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = "https://www.okx.com",
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._base_url = base_url.rstrip("/")

        # Initialize SDK clients (lazy for DRY_RUN)
        self._trade_api = None
        self._market_api = None
        self._account_api = None
        self._public_api = None

        # Cache for instrument specs
        self._instrument_cache: dict[str, InstrumentSpec] = {}

        if not self._is_dry_run():
            self._init_sdk_clients()

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    def _init_sdk_clients(self):
        """Initialize OKX SDK clients."""
        try:
            from okx.Trade import TradeAPI
            from okx.MarketData import MarketAPI
            from okx.Account import AccountAPI
            from okx.PublicData import PublicAPI

            flag = "1" if "demo" in self._base_url or self._is_dry_run() else "0"

            self._trade_api = TradeAPI(
                api_key=self._api_key,
                api_secret_key=self._api_secret,
                passphrase=self._passphrase,
                use_server_time=True,
                domain=self._base_url,
                flag=flag,
            )
            self._market_api = MarketAPI(
                api_key=self._api_key,
                api_secret_key=self._api_secret,
                passphrase=self._passphrase,
                use_server_time=False,
                domain=self._base_url,
                flag=flag,
            )
            self._account_api = AccountAPI(
                api_key=self._api_key,
                api_secret_key=self._api_secret,
                passphrase=self._passphrase,
                use_server_time=True,
                domain=self._base_url,
                flag=flag,
            )
            self._public_api = PublicAPI(
                api_key=self._api_key,
                api_secret_key=self._api_secret,
                passphrase=self._passphrase,
                use_server_time=False,
                domain=self._base_url,
                flag=flag,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to init OKX SDK: {e}") from e

    def _get_instrument_id(self, symbol: str) -> str:
        """Convert internal symbol to OKX instrument ID."""
        if symbol in SYMBOL_MAP:
            return SYMBOL_MAP[symbol]
        # If not in map, assume it's already an OKX instrument ID
        return symbol

    def _get_internal_symbol(self, instrument_id: str) -> str:
        """Convert OKX instrument ID to internal symbol."""
        if instrument_id in INSTRUMENT_TO_SYMBOL:
            return INSTRUMENT_TO_SYMBOL[instrument_id]
        return instrument_id

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """Return {symbol: mid_price} for the given symbols."""
        if self._is_dry_run() or self._market_api is None:
            # Return mock data for DRY_RUN
            return {s: 50000.0 for s in symbols}

        result = {}
        for symbol in symbols:
            inst_id = self._get_instrument_id(symbol)
            try:
                resp = self._market_api.get_ticker(instId=inst_id)
                if resp and "data" in resp and resp["data"]:
                    ticker = resp["data"][0]
                    bid = float(ticker.get("bidPx", 0))
                    ask = float(ticker.get("askPx", 0))
                    if bid > 0 and ask > 0:
                        result[symbol] = (bid + ask) / 2
            except Exception as e:
                logger.warning(f"Failed to get mid for {symbol}: {e}")
        return result

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """Return {"bids": [[px, sz], ...], "asks": [[px, sz], ...]}."""
        if self._is_dry_run() or self._market_api is None:
            return {
                "bids": [["50000.0", "1.0"]] * depth,
                "asks": [["50010.0", "1.0"]] * depth,
            }

        inst_id = self._get_instrument_id(symbol)
        try:
            resp = self._market_api.get_orderbook(instId=inst_id, sz=str(depth))
            if resp and "data" in resp and resp["data"]:
                book = resp["data"][0]
                bids = [[b[0], b[1]] for b in book.get("bids", [])[:depth]]
                asks = [[a[0], a[1]] for a in book.get("asks", [])[:depth]]
                return {"bids": bids, "asks": asks}
        except Exception as e:
            logger.warning(f"Failed to get L2 book for {symbol}: {e}")
        return {"bids": [], "asks": []}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Return recent trades, each with price/size/side/time."""
        if self._is_dry_run() or self._market_api is None:
            return []

        inst_id = self._get_instrument_id(symbol)
        try:
            resp = self._market_api.get_trades(instId=inst_id, limit=str(limit))
            if resp and "data" in resp:
                trades = []
                for t in resp["data"]:
                    trades.append({
                        "price": float(t.get("px", 0)),
                        "size": float(t.get("sz", 0)),
                        "side": t.get("side", "").lower(),
                        "time": t.get("ts", ""),
                    })
                return trades
        except Exception as e:
            logger.warning(f"Failed to get recent trades for {symbol}: {e}")
        return []

    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate (decimal, e.g., 0.0001)."""
        if self._is_dry_run() or self._public_api is None:
            return 0.0001

        inst_id = self._get_instrument_id(symbol)
        try:
            resp = self._public_api.get_funding_rate(instId=inst_id)
            if resp and "data" in resp and resp["data"]:
                return float(resp["data"][0].get("fundingRate", 0))
        except Exception as e:
            logger.warning(f"Failed to get funding rate for {symbol}: {e}")
        return 0.0

    def get_open_interest(self, symbol: str) -> float:
        """Return open interest (USD notional)."""
        if self._is_dry_run() or self._public_api is None:
            return 1000000.0

        inst_id = self._get_instrument_id(symbol)
        try:
            resp = self._public_api.get_open_interest(instId=inst_id)
            if resp and "data" in resp and resp["data"]:
                # OKX returns OI in contracts, need to convert to USD
                oi_contracts = float(resp["data"][0].get("oi", 0))
                # Get contract size from instrument spec
                spec = self.get_instrument_spec(symbol)
                # Get current price for USD notional
                mid = self.get_all_mids([symbol]).get(symbol, 0)
                return oi_contracts * spec.contract_size * mid
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
        """Place limit order. Return normalized result."""
        t0 = time.monotonic()
        inst_id = self._get_instrument_id(symbol)
        cid = client_order_id or f"okx_{int(time.time()*1000)}"

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

        # Determine posSide for hedge mode
        pos_side = "long" if is_buy else "short"
        side = "buy" if is_buy else "sell"

        # Convert qty to contracts
        spec = self.get_instrument_spec(symbol)
        contracts = str(int(qty / spec.contract_size)) if spec.contract_size > 0 else str(qty)

        # TIF mapping: OKX uses validType field
        # GTC = 1, IOC = 2, FOK = 3, GTX = 4
        tif_map = {"GTC": "1", "IOC": "2", "FOK": "3", "GTX": "4"}
        valid_type = tif_map.get(tif.upper(), "1")

        try:
            resp = self._trade_api.place_order(
                instId=inst_id,
                tdMode="cross",  # cross margin mode
                side=side,
                posSide=pos_side,
                ordType="limit",
                sz=contracts,
                px=str(limit_px),
                clOrdId=cid,
                reduceOnly="true" if reduce_only else "false",
                validType=valid_type,
            )

            if resp and "data" in resp and resp["data"]:
                order_data = resp["data"][0]
                ord_id = order_data.get("ordId", "")
                s_code = order_data.get("sCode", "")

                if s_code == "0":
                    return NormalizedOrderResult(
                        client_order_id=cid,
                        exchange_order_id=ord_id,
                        status="ack",
                        filled_qty=0.0,
                        avg_px=0.0,
                        fee_usd=0.0,
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                else:
                    raise OrderRejectedError(f"OKX order rejected: {order_data.get('sMsg', 'Unknown error')}")

        except OrderRejectedError:
            raise
        except Exception as e:
            raise OrderRejectedError(f"OKX order failed: {e}") from e

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Cancel order. Return True on success."""
        if self._is_dry_run():
            return True

        inst_id = self._get_instrument_id(symbol)
        try:
            resp = self._trade_api.cancel_order(instId=inst_id, ordId=exchange_order_id)
            if resp and "data" in resp and resp["data"]:
                return resp["data"][0].get("sCode") == "0"
        except Exception as e:
            logger.warning(f"Failed to cancel order {exchange_order_id}: {e}")
        return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        """Query order status."""
        inst_id = self._get_instrument_id(symbol)
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
            resp = self._trade_api.get_order(instId=inst_id, ordId=exchange_order_id)
            if resp and "data" in resp and resp["data"]:
                order = resp["data"][0]
                state = order.get("state", "")
                # OKX states: canceled, live, partially_filled, filled
                status_map = {
                    "live": "ack",
                    "filled": "filled",
                    "canceled": "canceled",
                    "partially_filled": "ack",
                }
                return NormalizedOrderResult(
                    client_order_id=order.get("clOrdId", ""),
                    exchange_order_id=order.get("ordId", ""),
                    status=status_map.get(state, "ack"),
                    filled_qty=float(order.get("fillSz", 0)),
                    avg_px=float(order.get("avgPx", 0)),
                    fee_usd=float(order.get("fee", 0)),
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
        if self._is_dry_run() or self._account_api is None:
            return NormalizedAccountState(
                equity_usd=10000.0,
                cash_usd=9000.0,
                positions={},
                open_orders=[],
            )

        try:
            # Get account balance
            balance_resp = self._account_api.get_account_balance()
            equity = 0.0
            cash = 0.0

            if balance_resp and "data" in balance_resp and balance_resp["data"]:
                for balance_data in balance_resp["data"]:
                    for detail in balance_data.get("details", []):
                        if detail.get("ccy") == "USDT":
                            equity = float(detail.get("eq", 0))
                            cash = float(detail.get("availBal", 0))
                            break

            # Get positions
            positions = {}
            pos_resp = self._account_api.get_positions(instType="SWAP")
            if pos_resp and "data" in pos_resp:
                for pos in pos_resp["data"]:
                    pos_qty = float(pos.get("pos", 0))
                    if pos_qty == 0:
                        continue

                    inst_id = pos.get("instId", "")
                    internal_symbol = self._get_internal_symbol(inst_id)

                    positions[internal_symbol] = NormalizedPosition(
                        symbol=internal_symbol,
                        qty=abs(pos_qty) * (1 if pos.get("posSide") == "long" else -1),
                        entry_px=float(pos.get("avgPx", 0)),
                        mark_px=float(pos.get("markPx", 0)),
                        unreal_pnl=float(pos.get("upl", 0)),
                        side=pos.get("posSide", "net"),
                    )

            # Get open orders
            open_orders = []
            orders_resp = self._trade_api.get_order_list(instType="SWAP")
            if orders_resp and "data" in orders_resp:
                for order in orders_resp["data"]:
                    inst_id = order.get("instId", "")
                    internal_symbol = self._get_internal_symbol(inst_id)

                    open_orders.append(
                        NormalizedOpenOrder(
                            exchange_order_id=order.get("ordId", ""),
                            client_order_id=order.get("clOrdId", ""),
                            symbol=internal_symbol,
                            side=order.get("side", "").lower(),
                            qty=float(order.get("sz", 0)),
                            limit_px=float(order.get("px", 0)),
                            filled_qty=float(order.get("fillSz", 0)),
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

        inst_id = self._get_instrument_id(symbol)

        if self._is_dry_run() or self._public_api is None:
            # Return default spec for DRY_RUN
            return InstrumentSpec(
                symbol=symbol,
                min_qty=1.0,
                qty_step=1.0,
                price_tick=0.1,
                contract_size=10.0,  # BTC contract size
                maker_fee=0.0002,
                taker_fee=0.0005,
            )

        try:
            resp = self._public_api.get_instruments(instType="SWAP", instId=inst_id)
            if resp and "data" in resp and resp["data"]:
                inst = resp["data"][0]
                spec = InstrumentSpec(
                    symbol=symbol,
                    min_qty=float(inst.get("minSz", 1)),
                    qty_step=float(inst.get("lotSz", 1)),
                    price_tick=float(inst.get("tickSz", 0.1)),
                    contract_size=float(inst.get("ctVal", 1)),
                    maker_fee=0.0002,  # Default maker fee
                    taker_fee=0.0005,  # Default taker fee
                )
                self._instrument_cache[symbol] = spec
                return spec
        except Exception as e:
            logger.warning(f"Failed to get instrument spec for {symbol}: {e}")

        # Return default spec if API fails
        return InstrumentSpec(
            symbol=symbol,
            min_qty=1.0,
            qty_step=1.0,
            price_tick=0.1,
            contract_size=10.0,
            maker_fee=0.0002,
            taker_fee=0.0005,
        )