# shared/exchange/adapters/hyperliquid.py
"""Hyperliquid exchange adapter.

Implements ExchangeAdapter interface for Hyperliquid perpetuals.
- Market data / Account state: Direct REST /info calls (httpx)
- Order placement / cancellation: hyperliquid-python-sdk (requires private key)
"""
from __future__ import annotations
import os
import time
import logging
from typing import Optional
import httpx
from shared.exchange.base import ExchangeAdapter, ExchangeError, OrderRejectedError, RateLimitError
from shared.exchange.models import (
    NormalizedOrderResult, NormalizedAccountState, NormalizedPosition,
    NormalizedOpenOrder, InstrumentSpec,
)

logger = logging.getLogger(__name__)


class HyperliquidAdapter(ExchangeAdapter):
    """
    Hyperliquid perpetuals adapter.
    Market data / Account: Direct REST /info calls (consistent with original market_data/portfolio_state).
    Order placement / cancellation: hyperliquid-python-sdk (requires private key signing).

    Note: DRY_RUN is read from os.environ on each method call, not at module import time,
    ensuring monkeypatch.setenv works in tests.
    """

    def __init__(self, private_key: str, account_address: str, base_url: str):
        self._account = account_address
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=10.0)
        self._private_key = private_key  # Kept for _build_sdk_client lazy initialization
        self._exchange_sdk = self._build_sdk_client(private_key, account_address, base_url)

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    def _build_sdk_client(self, private_key: str, account_address: str, base_url: str):
        if self._is_dry_run():
            return None
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            wallet = Account.from_key(private_key)
            return Exchange(wallet, base_url, account_address=account_address)
        except Exception as e:
            raise RuntimeError(f"Failed to init Hyperliquid SDK: {e}") from e

    def _post_info(self, payload: dict) -> dict:
        resp = self._http.post(f"{self._base_url}/info", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        data = self._post_info({"type": "allMids"})
        return {s: float(v) for s, v in data.items() if s in symbols}

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        data = self._post_info({"type": "l2Book", "coin": symbol, "nSigFigs": 5})
        return {"bids": data.get("levels", [[], []])[0][:depth],
                "asks": data.get("levels", [[], []])[1][:depth]}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        data = self._post_info({"type": "recentTrades", "coin": symbol})
        return data[:limit] if isinstance(data, list) else []

    def get_funding_rate(self, symbol: str) -> float:
        data = self._post_info({"type": "metaAndAssetCtxs"})
        meta, ctxs = data[0], data[1]
        for asset_meta, ctx in zip(meta.get("universe", []), ctxs):
            if asset_meta.get("name") == symbol:
                return float(ctx.get("funding", 0))
        return 0.0

    def get_open_interest(self, symbol: str) -> float:
        data = self._post_info({"type": "metaAndAssetCtxs"})
        meta, ctxs = data[0], data[1]
        for asset_meta, ctx in zip(meta.get("universe", []), ctxs):
            if asset_meta.get("name") == symbol:
                return float(ctx.get("openInterest", 0))
        return 0.0

    # ── Order Management ─────────────────────────────────────────────────────

    def place_limit(
        self, symbol: str, is_buy: bool, qty: float, limit_px: float,
        tif: str = "GTC", reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> NormalizedOrderResult:
        t0 = time.monotonic()
        cid = client_order_id or f"hl_{int(time.time()*1000)}"

        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id=cid, exchange_order_id=f"dry_{cid}",
                status="ack", filled_qty=0.0, avg_px=0.0, fee_usd=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        tif_map = {"GTC": "Gtc", "IOC": "Ioc", "ALO": "Alo"}
        try:
            res = self._exchange_sdk.order(
                symbol, is_buy, qty, limit_px,
                {"limit": {"tif": tif_map.get(tif, "Gtc")}},
                reduce_only=reduce_only,
            )
        except Exception as e:
            raise OrderRejectedError(str(e)) from e

        statuses = (res.get("response", {}).get("data", {}).get("statuses") or [{}])
        s = statuses[0]
        oid = str(s.get("resting", s.get("filled", {})).get("oid", ""))
        status = "filled" if "filled" in s else "ack"
        filled_qty = float(s.get("filled", {}).get("totalSz", 0))
        avg_px = float(s.get("filled", {}).get("avgPx", 0))

        return NormalizedOrderResult(
            client_order_id=cid, exchange_order_id=oid, status=status,
            filled_qty=filled_qty, avg_px=avg_px, fee_usd=0.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        if self._is_dry_run():
            return True
        try:
            res = self._exchange_sdk.cancel(symbol, int(exchange_order_id))
            return res.get("status") == "ok"
        except Exception as e:
            logger.warning(f"cancel_order failed: {e}")
            return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        data = self._post_info({"type": "orderStatus", "oid": int(exchange_order_id), "user": self._account})
        order = data.get("order", {})
        status_map = {"filled": "filled", "open": "ack", "canceled": "canceled"}
        raw_status = data.get("status", "unknown")
        return NormalizedOrderResult(
            client_order_id=order.get("clOid", ""),
            exchange_order_id=exchange_order_id,
            status=status_map.get(raw_status, "ack"),
            filled_qty=float(order.get("filledSz", 0)),
            avg_px=float(order.get("avgPx") or 0),
            fee_usd=0.0, latency_ms=0,
        )

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_state(self) -> NormalizedAccountState:
        data = self._post_info({"type": "clearinghouseState", "user": self._account})
        summary = data.get("marginSummary", {})
        equity = float(summary.get("accountValue", 0))
        cash = float(summary.get("totalRawUsd", 0))

        positions = {}
        for entry in data.get("assetPositions", []):
            pos = entry.get("position", {})
            symbol = pos.get("coin", "")
            qty = float(pos.get("szi", 0))
            if qty == 0:
                continue
            positions[symbol] = NormalizedPosition(
                symbol=symbol, qty=qty,
                entry_px=float(pos.get("entryPx") or 0),
                mark_px=float(pos.get("markPx") or 0),
                unreal_pnl=float(pos.get("unrealizedPnl", 0)),
                side="long" if qty > 0 else "short",
            )

        open_orders_raw = self._post_info({"type": "openOrders", "user": self._account})
        open_orders = [
            NormalizedOpenOrder(
                exchange_order_id=str(o.get("oid", "")),
                client_order_id=o.get("clOid", ""),
                symbol=o.get("coin", ""),
                side="buy" if o.get("side") == "B" else "sell",
                qty=float(o.get("sz", 0)),
                limit_px=float(o.get("limitPx", 0)),
                filled_qty=float(o.get("filledSz", 0)),
            )
            for o in (open_orders_raw if isinstance(open_orders_raw, list) else [])
        ]

        return NormalizedAccountState(equity_usd=equity, cash_usd=cash,
                                      positions=positions, open_orders=open_orders)

    # ── Instrument Specification ─────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Dynamically fetch. Cached by InstrumentRegistry, not called directly."""
        data = self._post_info({"type": "metaAndAssetCtxs"})
        for asset in data[0].get("universe", []):
            if asset.get("name") == symbol:
                sz_decimals = asset.get("szDecimals", 3)
                step = 10 ** (-sz_decimals)
                return InstrumentSpec(
                    symbol=symbol, min_qty=step, qty_step=step,
                    price_tick=0.1, contract_size=1.0,
                    maker_fee=0.0002, taker_fee=0.0005,
                )
        raise ValueError(f"Symbol {symbol} not found in HL universe")