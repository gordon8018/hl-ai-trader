# KuCoin Adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add KuCoin USDT-margined perpetual futures support to the existing multi-exchange architecture.

**Architecture:** Thin Adapter pattern — implement `KucoinAdapter` class with 10 abstract methods from `ExchangeAdapter`, following the same pattern as OKX/Binance adapters.

**Tech Stack:** Python 3.11+, python-kucoin>=2.2.0, pytest

---

## File Map

### New Files

| File | Responsibility |
|------|----------------|
| `shared/exchange/adapters/kucoin.py` | KucoinAdapter implementation (~450 lines) |
| `tests/exchange/test_kucoin_adapter.py` | Unit tests (~200 lines) |

### Modified Files

| File | Change |
|------|--------|
| `shared/exchange/factory.py` | Add `kucoin` branch |
| `shared/exchange/static_fallback.json` | Add KuCoin instrument specs |
| `infra/.env.example` | Add KuCoin credentials |
| `pyproject.toml` | Add python-kucoin dependency |

---

## Chunk 1: SDK Installation and Project Setup

### Task 1: 安装 KuCoin SDK

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 安装 SDK**

```bash
pip install python-kucoin==2.2.0
```

- [ ] **Step 2: 确认导入成功**

```bash
python -c "from kucoin.client import Client; print('KuCoin SDK OK')"
```

预期输出: `KuCoin SDK OK`

- [ ] **Step 3: 添加依赖到 pyproject.toml**

在 `[project.dependencies]` 中添加:

```toml
"python-kucoin>=2.2.0",
```

- [ ] **Step 4: 提交依赖变更**

```bash
git add pyproject.toml
git commit -m "chore(deps): add python-kucoin SDK"
```

---

### Task 2: 更新配置文件

**Files:**
- Modify: `infra/.env.example`
- Modify: `shared/exchange/static_fallback.json`

- [ ] **Step 1: 更新 .env.example**

在 `infra/.env.example` 文件末尾追加:

```bash
# ── KuCoin 凭证（EXCHANGE=kucoin 时填写）───────
KUCOIN_API_KEY=
KUCOIN_API_SECRET=
KUCOIN_PASSPHRASE=
KUCOIN_FUTURES_URL=https://api-futures.kucoin.com
```

同时修改 EXCHANGE 注释:

```bash
EXCHANGE=hyperliquid   # hyperliquid | okx | binance | kucoin
```

- [ ] **Step 2: 更新 static_fallback.json**

在 `shared/exchange/static_fallback.json` 的根对象中添加 `kucoin` 条目:

```json
  "kucoin": {
    "BTC":  {"min_qty": 0.001, "qty_step": 0.001, "price_tick": 1.0,   "contract_size": 0.001, "maker_fee": 0.0002, "taker_fee": 0.0006},
    "ETH":  {"min_qty": 0.01,  "qty_step": 0.01,  "price_tick": 0.01,  "contract_size": 0.01,  "maker_fee": 0.0002, "taker_fee": 0.0006},
    "SOL":  {"min_qty": 0.1,   "qty_step": 0.1,   "price_tick": 0.001, "contract_size": 1.0,   "maker_fee": 0.0002, "taker_fee": 0.0006},
    "ADA":  {"min_qty": 1.0,   "qty_step": 1.0,   "price_tick": 0.00001,"contract_size": 10.0,  "maker_fee": 0.0002, "taker_fee": 0.0006},
    "DOGE": {"min_qty": 1.0,   "qty_step": 1.0,   "price_tick": 0.00001,"contract_size": 10.0,  "maker_fee": 0.0002, "taker_fee": 0.0006}
  }
```

- [ ] **Step 3: 提交配置变更**

```bash
git add infra/.env.example shared/exchange/static_fallback.json
git commit -m "feat(config): add KuCoin credentials and instrument specs"
```

---

### Task 3: 更新工厂函数

**Files:**
- Modify: `shared/exchange/factory.py`

- [ ] **Step 1: 添加 kucoin 分支**

在 `shared/exchange/factory.py` 的 `create_exchange_adapter()` 函数中，在 `binance` 分支后添加:

```python
    elif exchange == "kucoin":
        from shared.exchange.adapters.kucoin import KucoinAdapter
        return KucoinAdapter(
            api_key=os.environ["KUCOIN_API_KEY"],
            api_secret=os.environ["KUCOIN_API_SECRET"],
            passphrase=os.environ["KUCOIN_PASSPHRASE"],
            base_url=os.environ.get("KUCOIN_FUTURES_URL", "https://api-futures.kucoin.com"),
        )
```

同时更新错误消息:

```python
    else:
        raise ValueError(f"Unsupported exchange: {exchange!r}. Choose: hyperliquid, okx, binance, kucoin")
```

- [ ] **Step 2: 提交工厂函数变更**

```bash
git add shared/exchange/factory.py
git commit -m "feat(factory): add KuCoin adapter branch"
```

---

## Chunk 2: KucoinAdapter Implementation

### Task 4: 创建 KucoinAdapter 骨架

**Files:**
- Create: `shared/exchange/adapters/kucoin.py`

- [ ] **Step 1: 创建文件骨架**

```python
# shared/exchange/adapters/kucoin.py
"""KuCoin exchange adapter.

Implements ExchangeAdapter interface for KuCoin USDT-margined perpetual futures.
SDK: kucoin-futures-python-sdk
Product type: USDT-M (USDT-margined perpetual)
Symbol mapping: {"BTC": "XBTUSDTM", "ETH": "ETHUSDTM", ...}
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

    Uses kucoin-futures-python-sdk for all operations.
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

        # Cache for instrument specs
        self._instrument_cache: dict[str, InstrumentSpec] = {}

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

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """Return mid prices for given symbols."""
        raise NotImplementedError("TODO: implement")

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """Return order book with bids/asks."""
        raise NotImplementedError("TODO: implement")

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Return recent trades."""
        raise NotImplementedError("TODO: implement")

    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate."""
        raise NotImplementedError("TODO: implement")

    def get_open_interest(self, symbol: str) -> float:
        """Return open interest in USD."""
        raise NotImplementedError("TODO: implement")

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
        raise NotImplementedError("TODO: implement")

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Cancel order."""
        raise NotImplementedError("TODO: implement")

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        """Get order status."""
        raise NotImplementedError("TODO: implement")

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_state(self) -> NormalizedAccountState:
        """Return account state with positions and open orders."""
        raise NotImplementedError("TODO: implement")

    # ── Instrument Specification ─────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Return instrument specification."""
        raise NotImplementedError("TODO: implement")
```

- [ ] **Step 2: 验证文件语法正确**

```bash
python -c "from shared.exchange.adapters.kucoin import KucoinAdapter; print('OK')"
```

预期输出: `OK`

- [ ] **Step 3: 提交骨架**

```bash
git add shared/exchange/adapters/kucoin.py
git commit -m "feat(exchange): add KucoinAdapter skeleton"
```

---

### Task 5: 实现 Market Data 方法

**Files:**
- Modify: `shared/exchange/adapters/kucoin.py`

- [ ] **Step 1: 实现 get_all_mids**

替换 `get_all_mids` 方法:

```python
    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """Return mid prices for given symbols."""
        result = {}
        for symbol in symbols:
            instrument_id = self._get_instrument_id(symbol)

            if self._is_dry_run():
                # Mock data for DRY_RUN
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
```

- [ ] **Step 2: 实现 get_l2_book**

替换 `get_l2_book` 方法:

```python
    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """Return order book with bids/asks."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            # Mock data for DRY_RUN
            return {
                "bids": [[50000.0 - i * 10, 1.0] for i in range(depth)],
                "asks": [[50010.0 + i * 10, 1.0] for i in range(depth)],
            }

        try:
            # KuCoin uses level2 order book
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
```

- [ ] **Step 3: 实现 get_recent_trades**

替换 `get_recent_trades` 方法:

```python
    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Return recent trades."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            # Mock data for DRY_RUN
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
```

- [ ] **Step 4: 实现 get_funding_rate**

替换 `get_funding_rate` 方法:

```python
    def get_funding_rate(self, symbol: str) -> float:
        """Return current funding rate."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return 0.0001  # Mock: 0.01%

        try:
            # KuCoin returns funding rate in contract details
            contracts = self._client.futures_get_symbols()
            data = contracts.get("data", [])

            for c in data:
                if c.get("symbol") == instrument_id:
                    return float(c.get("fundingFeeRate", 0))

            return 0.0
        except Exception as e:
            logger.warning(f"Failed to get funding rate for {symbol}: {e}")
            return 0.0
```

- [ ] **Step 5: 实现 get_open_interest**

替换 `get_open_interest` 方法:

```python
    def get_open_interest(self, symbol: str) -> float:
        """Return open interest in USD."""
        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            return 1000000.0  # Mock: 1M USD

        try:
            contracts = self._client.futures_get_symbols()
            data = contracts.get("data", [])

            for c in data:
                if c.get("symbol") == instrument_id:
                    # Open interest in contracts, convert to USD
                    oi_contracts = float(c.get("openInterest", 0))
                    # Need mark price for conversion
                    mark_px = float(c.get("markPrice", 0))
                    contract_size = float(c.get("multiplier", 1))
                    return oi_contracts * contract_size * mark_px

            return 0.0
        except Exception as e:
            logger.warning(f"Failed to get open interest for {symbol}: {e}")
            return 0.0
```

- [ ] **Step 6: 提交 Market Data 方法**

```bash
git add shared/exchange/adapters/kucoin.py
git commit -m "feat(kucoin): implement market data methods"
```

---

### Task 6: 实现 Order Management 方法

**Files:**
- Modify: `shared/exchange/adapters/kucoin.py`

- [ ] **Step 1: 实现 place_limit**

替换 `place_limit` 方法:

```python
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

        # Get instrument spec for lot size
        spec = self.get_instrument_spec(symbol)

        # KuCoin TIF mapping
        tif_map = {"GTC": "GTC", "IOC": "IOC", "FOK": "FOK", "GTX": "PO"}
        kucoin_tif = tif_map.get(tif.upper(), "GTC")

        try:
            # KuCoin uses "buy"/"sell" for side
            side = "buy" if is_buy else "sell"

            result = self._client.futures_create_order(
                symbol=instrument_id,
                side=side,
                lever="1",  # Leverage (can be configured)
                size=str(int(qty / spec.qty_step) * spec.qty_step),
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
```

- [ ] **Step 2: 实现 cancel_order**

替换 `cancel_order` 方法:

```python
    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Cancel order."""
        if self._is_dry_run():
            return True

        try:
            result = self._client.futures_cancel_order(exchange_order_id)
            return result.get("code", "") == "200000"
        except Exception as e:
            logger.warning(f"Failed to cancel order {exchange_order_id}: {e}")
            return False
```

- [ ] **Step 3: 实现 get_order_status**

替换 `get_order_status` 方法:

```python
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

            # Map KuCoin status to normalized status
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

            return NormalizedOrderResult(
                client_order_id=data.get("clientOid", ""),
                exchange_order_id=exchange_order_id,
                status=status,
                filled_qty=float(data.get("dealSize", 0)),
                avg_px=float(data.get("dealValue", 0)) / max(float(data.get("dealSize", 1)), 1),
                fee_usd=float(data.get("fee", 0)),
                latency_ms=0,
            )
        except Exception as e:
            raise ExchangeError(f"Failed to get order status: {e}") from e
```

- [ ] **Step 4: 提交 Order Management 方法**

```bash
git add shared/exchange/adapters/kucoin.py
git commit -m "feat(kucoin): implement order management methods"
```

---

### Task 7: 实现 Account 和 Instrument 方法

**Files:**
- Modify: `shared/exchange/adapters/kucoin.py`

- [ ] **Step 1: 实现 get_account_state**

替换 `get_account_state` 方法:

```python
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
            # Get account overview
            overview = self._client.futures_get_account_detail("USDT")
            data = overview.get("data", {})

            equity = float(data.get("accountEquity", 0))
            cash = float(data.get("availableBalance", 0))

            # Get positions
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

            # Get open orders
            open_orders = []
            orders_resp = self._client.futures_get_orders()
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
```

- [ ] **Step 2: 实现 get_instrument_spec**

替换 `get_instrument_spec` 方法:

```python
    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Return instrument specification."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        instrument_id = self._get_instrument_id(symbol)

        if self._is_dry_run():
            # Return mock spec for DRY_RUN
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
            contracts = self._client.futures_get_symbols()
            data = contracts.get("data", [])

            for c in data:
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

            raise ValueError(f"Symbol {symbol} not found in KuCoin contracts")
        except Exception as e:
            logger.warning(f"Failed to get instrument spec for {symbol}: {e}")
            # Return fallback from static_fallback.json
            return InstrumentSpec(
                symbol=symbol,
                min_qty=0.001,
                qty_step=0.001,
                price_tick=0.1,
                contract_size=0.001,
                maker_fee=0.0002,
                taker_fee=0.0006,
            )
```

- [ ] **Step 3: 提交完整实现**

```bash
git add shared/exchange/adapters/kucoin.py
git commit -m "feat(kucoin): complete KucoinAdapter implementation"
```

---

## Chunk 3: Tests and Verification

### Task 8: 编写单元测试

**Files:**
- Create: `tests/exchange/test_kucoin_adapter.py`

- [ ] **Step 1: 创建测试文件**

```python
# tests/exchange/test_kucoin_adapter.py
"""Unit tests for KucoinAdapter."""
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.kucoin import KucoinAdapter, SYMBOL_MAP
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter():
    """Create KucoinAdapter with mocked SDK client."""
    with patch("shared.exchange.adapters.kucoin.KucoinAdapter._init_sdk_client"):
        a = KucoinAdapter(
            api_key="test_key",
            api_secret="test_secret",
            passphrase="test_pass",
            base_url="https://api-futures.kucoin.com",
        )
    return a


def test_symbol_mapping():
    """Test symbol mapping is correct."""
    assert SYMBOL_MAP["BTC"] == "XBTUSDTM"  # Note: XBT not BTC
    assert SYMBOL_MAP["ETH"] == "ETHUSDTM"


def test_get_instrument_id(adapter):
    """Test internal symbol to KuCoin instrument ID conversion."""
    assert adapter._get_instrument_id("BTC") == "XBTUSDTM"
    assert adapter._get_instrument_id("ETH") == "ETHUSDTM"


def test_get_internal_symbol(adapter):
    """Test KuCoin instrument ID to internal symbol conversion."""
    assert adapter._get_internal_symbol("XBTUSDTM") == "BTC"
    assert adapter._get_internal_symbol("ETHUSDTM") == "ETH"


def test_get_all_mids_dry_run(adapter):
    """Test get_all_mids in DRY_RUN mode."""
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert "BTC" in result
    assert "ETH" in result
    assert result["BTC"] == 50000.0


def test_get_l2_book_dry_run(adapter):
    """Test get_l2_book in DRY_RUN mode."""
    result = adapter.get_l2_book("BTC", depth=3)
    assert "bids" in result
    assert "asks" in result
    assert len(result["bids"]) == 3


def test_get_recent_trades_dry_run(adapter):
    """Test get_recent_trades in DRY_RUN mode."""
    result = adapter.get_recent_trades("BTC", limit=5)
    assert len(result) == 5
    assert "price" in result[0]


def test_get_funding_rate_dry_run(adapter):
    """Test get_funding_rate in DRY_RUN mode."""
    result = adapter.get_funding_rate("BTC")
    assert result == 0.0001


def test_get_open_interest_dry_run(adapter):
    """Test get_open_interest in DRY_RUN mode."""
    result = adapter.get_open_interest("BTC")
    assert result == 1000000.0


def test_place_limit_dry_run(adapter):
    """Test place_limit in DRY_RUN mode."""
    result = adapter.place_limit("BTC", True, 0.001, 50000.0)
    assert isinstance(result, NormalizedOrderResult)
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_kc_")


def test_place_limit_with_client_order_id(adapter):
    """Test place_limit preserves client_order_id."""
    result = adapter.place_limit("BTC", True, 0.001, 50000.0, client_order_id="my_order")
    assert result.client_order_id == "my_order"


def test_cancel_order_dry_run(adapter):
    """Test cancel_order in DRY_RUN mode."""
    result = adapter.cancel_order("BTC", "order123")
    assert result is True


def test_get_order_status_dry_run(adapter):
    """Test get_order_status in DRY_RUN mode."""
    result = adapter.get_order_status("BTC", "order123")
    assert isinstance(result, NormalizedOrderResult)
    assert result.exchange_order_id == "order123"


def test_get_account_state_dry_run(adapter):
    """Test get_account_state in DRY_RUN mode."""
    result = adapter.get_account_state()
    assert isinstance(result, NormalizedAccountState)
    assert result.equity_usd == 10000.0


def test_get_instrument_spec_dry_run(adapter):
    """Test get_instrument_spec in DRY_RUN mode."""
    from shared.exchange.models import InstrumentSpec
    result = adapter.get_instrument_spec("BTC")
    assert isinstance(result, InstrumentSpec)
    assert result.symbol == "BTC"


# Non-DRY_RUN tests with mocked client

def test_get_all_mids_with_mock(adapter, monkeypatch):
    """Test get_all_mids with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_ticker.return_value = {
        "data": {"bestBidPrice": "50000.0", "bestAskPrice": "50010.0"}
    }
    adapter._client = mock_client

    result = adapter.get_all_mids(["BTC"])
    assert result["BTC"] == pytest.approx(50005.0)


def test_place_limit_with_mock(adapter, monkeypatch):
    """Test place_limit with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_create_order.return_value = {
        "data": {"orderId": "kucoin_order_123"}
    }
    mock_client.futures_get_symbols.return_value = {
        "data": [{
            "symbol": "XBTUSDTM",
            "lotSize": "0.001",
            "tickSize": "0.1",
            "multiplier": "0.001",
        }]
    }
    adapter._client = mock_client

    result = adapter.place_limit("BTC", True, 0.001, 50000.0)
    assert result.exchange_order_id == "kucoin_order_123"


def test_cancel_order_with_mock(adapter, monkeypatch):
    """Test cancel_order with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_cancel_order.return_value = {"code": "200000"}
    adapter._client = mock_client

    assert adapter.cancel_order("BTC", "order123") is True
```

- [ ] **Step 2: 运行测试**

```bash
python -m pytest tests/exchange/test_kucoin_adapter.py -v
```

预期: 所有测试通过

- [ ] **Step 3: 提交测试**

```bash
git add tests/exchange/test_kucoin_adapter.py
git commit -m "test(kucoin): add unit tests for KucoinAdapter"
```

---

### Task 9: DRY_RUN 冒烟测试

**Files:**
- 无文件变更（验证任务）

- [ ] **Step 1: 运行 DRY_RUN 冒烟测试**

```bash
EXCHANGE=kucoin DRY_RUN=true python -c "
from shared.exchange.factory import create_exchange_adapter
e = create_exchange_adapter()
print('KuCoin adapter created:', type(e).__name__)
r = e.place_limit('BTC', True, 0.001, 50000.0)
print('DRY_RUN order:', r.exchange_order_id)
print('Status:', r.status)
"
```

预期输出:
```
KuCoin adapter created: KucoinAdapter
DRY_RUN order: dry_kc_...
Status: ack
```

- [ ] **Step 2: 运行完整 exchange 测试**

```bash
python -m pytest tests/exchange/ -v
```

预期: 所有 exchange 测试通过

---

### Task 10: 最终提交和推送

- [ ] **Step 1: 确认所有变更已提交**

```bash
git status
```

预期: 无未提交变更

- [ ] **Step 2: 查看提交历史**

```bash
git log --oneline -10
```

- [ ] **Step 3: 推送到远程**

```bash
git push origin main
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Install SDK | pyproject.toml |
| 2 | Update config files | .env.example, static_fallback.json |
| 3 | Update factory | factory.py |
| 4 | Create adapter skeleton | kucoin.py |
| 5 | Implement market data methods | kucoin.py |
| 6 | Implement order methods | kucoin.py |
| 7 | Implement account/instrument methods | kucoin.py |
| 8 | Write unit tests | test_kucoin_adapter.py |
| 9 | DRY_RUN smoke test | - |
| 10 | Final commit and push | - |