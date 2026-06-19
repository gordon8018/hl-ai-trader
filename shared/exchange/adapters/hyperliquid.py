# shared/exchange/adapters/hyperliquid.py
"""Hyperliquid exchange adapter.

Implements ExchangeAdapter interface for Hyperliquid perpetuals.
- Market data / Account state: Direct REST /info calls (httpx)
- Order placement / cancellation: hyperliquid-python-sdk (requires private key)
"""
from __future__ import annotations
import asyncio
import json
import os
import threading
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
        self._ws_url = self._derive_ws_url(self._base_url)
        self._http = httpx.Client(timeout=10.0)
        self._private_key = private_key  # Kept for _build_sdk_client lazy initialization
        self._exchange_sdk = self._build_sdk_client(private_key, account_address, base_url)
        self._ws_mid_cache: dict[str, float] = {}
        self._ws_mid_cache_ts = 0.0
        self._ws_lock = threading.Lock()
        self._ws_stop = threading.Event()
        self._ws_thread: Optional[threading.Thread] = None
        if self._ws_all_mids_enabled():
            self._ensure_ws_thread()

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    @staticmethod
    def _derive_ws_url(base_url: str) -> str:
        text = (base_url or "https://api.hyperliquid.xyz").rstrip("/")
        if text.startswith("https://"):
            return "wss://" + text[len("https://"):] + "/ws"
        if text.startswith("http://"):
            return "ws://" + text[len("http://"):] + "/ws"
        if text.startswith("wss://") or text.startswith("ws://"):
            return text if text.endswith("/ws") else text + "/ws"
        return "wss://api.hyperliquid.xyz/ws"

    @staticmethod
    def _ws_all_mids_enabled() -> bool:
        return os.environ.get("HL_WS_ALL_MIDS_ENABLED", "true").lower() == "true"

    @staticmethod
    def _ws_stale_seconds() -> float:
        try:
            return float(os.environ.get("HL_WS_STALE_SECONDS", "5.0"))
        except Exception:
            return 5.0

    def _ensure_ws_thread(self) -> None:
        if self._ws_thread is not None and self._ws_thread.is_alive():
            return
        self._ws_thread = threading.Thread(target=self._run_ws_loop, name="hl-allmids-ws", daemon=True)
        self._ws_thread.start()

    def _run_ws_loop(self) -> None:
        try:
            asyncio.run(self._ws_loop())
        except Exception as e:
            logger.warning("hyperliquid ws loop stopped: %s", e)

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except Exception as e:
            logger.warning("websockets dependency unavailable, fallback to REST allMids: %s", e)
            return

        while not self._ws_stop.is_set():
            try:
                async with websockets.connect(self._ws_url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))
                    while not self._ws_stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        try:
                            self._handle_ws_message(json.loads(raw))
                        except Exception as decode_err:
                            logger.debug("ignore ws message decode failure: %s", decode_err)
            except Exception as e:
                logger.warning("hyperliquid allMids ws disconnected, retrying: %s", e)
                await asyncio.sleep(1.0)

    def _handle_ws_message(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        channel = payload.get("channel") or payload.get("type")
        if channel not in {"allMids", None}:
            return
        mids = data.get("mids") if isinstance(data, dict) else None
        if not isinstance(mids, dict):
            return
        parsed: dict[str, float] = {}
        for sym, val in mids.items():
            try:
                parsed[str(sym)] = float(val)
            except Exception:
                continue
        if not parsed:
            return
        with self._ws_lock:
            self._ws_mid_cache.update(parsed)
            self._ws_mid_cache_ts = time.time()

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
        if self._ws_all_mids_enabled():
            if self._ws_thread is None or not self._ws_thread.is_alive():
                self._ensure_ws_thread()
            with self._ws_lock:
                cache_age = time.time() - self._ws_mid_cache_ts if self._ws_mid_cache_ts > 0 else float("inf")
                fresh = cache_age <= self._ws_stale_seconds()
                if fresh:
                    cached = {s: self._ws_mid_cache[s] for s in symbols if s in self._ws_mid_cache}
                    if len(cached) == len(set(symbols)):
                        return cached
        data = self._post_info({"type": "allMids"})
        return {s: float(v) for s, v in data.items() if s in symbols}

    def get_l2_book(self, symbol: str, depth: int = 5, n_sig_figs: int = 5, mantissa: Optional[int] = None) -> dict:
        payload = {"type": "l2Book", "coin": symbol, "nSigFigs": n_sig_figs}
        if mantissa is not None:
            payload["mantissa"] = mantissa
        data = self._post_info(payload)
        return {"bids": data.get("levels", [[], []])[0][:depth],
                "asks": data.get("levels", [[], []])[1][:depth]}

    def get_l2_book_raw(self, symbol: str, n_sig_figs: Optional[int] = None, mantissa: Optional[int] = None) -> dict:
        """Return raw l2Book response for custom processing.

        Used by market_data service to apply parse_l2_metrics.
        """
        payload = {"type": "l2Book", "coin": symbol}
        if n_sig_figs is not None:
            payload["nSigFigs"] = n_sig_figs
        if mantissa is not None:
            payload["mantissa"] = mantissa
        return self._post_info(payload)

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

    def get_meta_and_asset_ctxs_raw(self) -> list:
        """Return raw metaAndAssetCtxs response for batch processing.

        Used by market_data service to efficiently fetch funding/openInterest
        for all symbols in a single API call.
        """
        return self._post_info({"type": "metaAndAssetCtxs"})

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

        statuses = (res.get("response", {}).get("data", {}).get("statuses") or [])
        if not statuses:
            raise OrderRejectedError(f"empty order response: {res}")
        s = statuses[0]
        if "error" in s:
            raise OrderRejectedError(str(s.get("error") or "order rejected"))
        order_info = s.get("resting") or s.get("filled") or {}
        oid_value = order_info.get("oid")
        if oid_value is None or str(oid_value).strip() == "":
            raise OrderRejectedError(f"missing exchange order id: {res}")
        oid = str(oid_value)
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
            oid = int(exchange_order_id)
        except ValueError as e:
            raise ExchangeError(f"Invalid order ID: {exchange_order_id}") from e
        try:
            res = self._exchange_sdk.cancel(symbol, oid)
            return res.get("status") == "ok"
        except Exception as e:
            logger.warning(f"cancel_order failed: {e}")
            return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        try:
            oid = int(exchange_order_id)
        except ValueError as e:
            raise ExchangeError(f"Invalid order ID: {exchange_order_id}") from e
        data = self._post_info({"type": "orderStatus", "oid": oid, "user": self._account})
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

    def _get_unified_usdc_available(self) -> float:
        """Return available USDC exposed via Hyperliquid spot state for unified accounts."""
        try:
            data = self._post_info({"type": "spotClearinghouseState", "user": self._account})
        except Exception as e:
            logger.warning("spotClearinghouseState unavailable for unified balance fallback: %s", e)
            return 0.0
        if not isinstance(data, dict):
            return 0.0

        available_by_token = {}
        for token_id, value in data.get("tokenToAvailableAfterMaintenance") or []:
            try:
                available_by_token[int(token_id)] = float(value)
            except Exception:
                continue

        for balance in data.get("balances") or []:
            if balance.get("coin") != "USDC":
                continue
            try:
                token_id = int(balance.get("token", 0))
            except Exception:
                token_id = 0
            if token_id in available_by_token:
                return available_by_token[token_id]
            try:
                total = float(balance.get("total", 0) or 0)
                hold = float(balance.get("hold", 0) or 0)
                return max(0.0, total - hold)
            except Exception:
                return 0.0
        return 0.0

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

        unified_usdc_available = self._get_unified_usdc_available()
        if unified_usdc_available > max(equity, cash, 0.0):
            equity = unified_usdc_available
            cash = unified_usdc_available

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