# Multi-Exchange Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Hyperliquid 系统上引入 Thin Adapter 抽象层，支持通过 `EXCHANGE` 环境变量切换至 OKX 或 Binance 永续合约交易。

**Architecture:** 新建 `shared/exchange/` 目录，定义 `ExchangeAdapter` 抽象基类和归一化数据模型；将现有 HL 代码迁移为 `HyperliquidAdapter`；三个业务服务（market_data / execution / portfolio_state）改为调用统一接口，不再直接依赖 HL SDK 或 httpx 原始调用。

**Tech Stack:** Python 3.11+, httpx, hyperliquid-python-sdk, python-okx, binance-futures-connector, Redis, Pydantic, pytest

---

## File Map

### 新建文件

| 文件 | 职责 |
|------|------|
| `shared/exchange/__init__.py` | 包入口，re-export ExchangeAdapter + factory |
| `shared/exchange/base.py` | ExchangeAdapter 抽象基类 + 异常类型 |
| `shared/exchange/models.py` | 归一化数据模型（NormalizedOrderResult 等） |
| `shared/exchange/factory.py` | 根据 EXCHANGE 环境变量实例化 adapter |
| `shared/exchange/instrument_registry.py` | 合约规格缓存（Redis + 静态兜底） |
| `shared/exchange/static_fallback.json` | 合约规格静态兜底数据 |
| `shared/exchange/adapters/__init__.py` | adapters 包入口 |
| `shared/exchange/adapters/hyperliquid.py` | HyperliquidAdapter（迁移自 shared/hl/client.py） |
| `shared/exchange/adapters/okx.py` | OKXAdapter |
| `shared/exchange/adapters/binance.py` | BinanceAdapter |
| `tests/exchange/__init__.py` | 测试包入口 |
| `tests/exchange/test_models.py` | 归一化模型单元测试 |
| `tests/exchange/test_factory.py` | 工厂函数路由测试 |
| `tests/exchange/test_instrument_registry.py` | 缓存命中/缺失/兜底三路径测试 |
| `tests/exchange/test_hyperliquid_adapter.py` | HyperliquidAdapter 测试（mock SDK） |
| `tests/exchange/test_okx_adapter.py` | OKXAdapter 测试（mock python-okx） |
| `tests/exchange/test_binance_adapter.py` | BinanceAdapter 测试（mock binance SDK） |

### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `services/market_data/app.py` | 替换直接 httpx 调用为 adapter 接口 |
| `services/execution/app.py` | 替换 HyperliquidClient 调用为 adapter 接口 |
| `services/portfolio_state/app.py` | 替换直接 httpx 调用为 adapter 接口 |
| `services/ai_decision/config_loader.py` | 加入 exchange_overrides 合并逻辑 |
| `config/trading_params.json` | 新增 exchange_overrides 块 |
| `infra/.env.example` | 新增 EXCHANGE、OKX_*、BINANCE_* 变量 |

---

## Chunk 1: 基础层 — 模型、接口、工厂、合约规格缓存

### Task 1: 归一化数据模型

**Files:**
- Create: `shared/exchange/models.py`
- Create: `tests/exchange/test_models.py`

- [ ] **Step 1: 创建测试文件，写出模型实例化和字段验证测试**

```python
# tests/exchange/test_models.py
from shared.exchange.models import (
    NormalizedTicker, NormalizedPosition, NormalizedOpenOrder,
    NormalizedAccountState, NormalizedOrderResult, InstrumentSpec,
)

def test_normalized_ticker_fields():
    t = NormalizedTicker(symbol="BTC", mid_px=50000.0, bid=49999.0, ask=50001.0, spread_bps=0.4)
    assert t.symbol == "BTC"
    assert t.spread_bps == pytest.approx(0.4)

def test_normalized_position_side_long():
    p = NormalizedPosition(symbol="ETH", qty=1.0, entry_px=3000.0, mark_px=3100.0, unreal_pnl=100.0, side="long")
    assert p.side == "long"
    assert p.qty > 0

def test_normalized_position_side_short():
    p = NormalizedPosition(symbol="BTC", qty=-0.1, entry_px=50000.0, mark_px=49000.0, unreal_pnl=100.0, side="short")
    assert p.qty < 0

def test_normalized_order_result_statuses():
    for status in ("ack", "filled", "rejected", "canceled"):
        r = NormalizedOrderResult(
            client_order_id="c1", exchange_order_id="e1", status=status,
            filled_qty=0.0, avg_px=0.0, fee_usd=0.0, latency_ms=10
        )
        assert r.status == status

def test_instrument_spec_fields():
    spec = InstrumentSpec(
        symbol="BTC", min_qty=0.001, qty_step=0.001,
        price_tick=0.1, contract_size=1.0, maker_fee=0.0002, taker_fee=0.0004
    )
    assert spec.contract_size == 1.0
```

- [ ] **Step 2: 运行测试，确认失败（模块不存在）**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
python -m pytest tests/exchange/test_models.py -v 2>&1 | head -20
```

预期：`ModuleNotFoundError: No module named 'shared.exchange'`

- [ ] **Step 3: 创建包目录和模型文件**

```bash
mkdir -p shared/exchange/adapters
touch shared/exchange/__init__.py shared/exchange/adapters/__init__.py
touch tests/exchange/__init__.py
```

```python
# shared/exchange/models.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class NormalizedTicker:
    symbol: str
    mid_px: float
    bid: float
    ask: float
    spread_bps: float


@dataclass
class NormalizedPosition:
    symbol: str
    qty: float          # 正=多，负=空
    entry_px: float
    mark_px: float
    unreal_pnl: float
    side: str           # "long" | "short" | "flat"


@dataclass
class NormalizedOpenOrder:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: str           # "buy" | "sell"
    qty: float
    limit_px: float
    filled_qty: float


@dataclass
class NormalizedAccountState:
    equity_usd: float
    cash_usd: float
    positions: dict[str, NormalizedPosition] = field(default_factory=dict)
    open_orders: list[NormalizedOpenOrder] = field(default_factory=list)


@dataclass
class NormalizedOrderResult:
    client_order_id: str
    exchange_order_id: str
    status: str         # "ack" | "filled" | "rejected" | "canceled"
    filled_qty: float
    avg_px: float
    fee_usd: float
    latency_ms: int


@dataclass
class InstrumentSpec:
    symbol: str
    min_qty: float
    qty_step: float
    price_tick: float
    contract_size: float  # OKX 合约面值；Binance/HL 填 1.0
    maker_fee: float
    taker_fee: float
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_models.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/ tests/exchange/
git commit -m "feat(exchange): add normalized data models and test package"
```

---

### Task 2: ExchangeAdapter 抽象基类和异常类型

**Files:**
- Create: `shared/exchange/base.py`

- [ ] **Step 1: 编写接口存在性测试**

在 `tests/exchange/test_models.py` 尾部追加：

```python
import inspect
from shared.exchange.base import ExchangeAdapter, ExchangeError, OrderRejectedError, RateLimitError

def test_exchange_adapter_is_abstract():
    assert inspect.isabstract(ExchangeAdapter)

def test_exchange_error_hierarchy():
    assert issubclass(OrderRejectedError, ExchangeError)
    assert issubclass(RateLimitError, ExchangeError)

def test_adapter_has_required_methods():
    required = [
        "get_all_mids", "get_l2_book", "get_recent_trades",
        "get_funding_rate", "get_open_interest",
        "place_limit", "cancel_order", "get_order_status",
        "get_account_state", "get_instrument_spec",
    ]
    for method in required:
        assert hasattr(ExchangeAdapter, method), f"Missing method: {method}"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/exchange/test_models.py::test_exchange_adapter_is_abstract -v
```

预期：`ImportError`

- [ ] **Step 3: 实现抽象基类**

```python
# shared/exchange/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from shared.exchange.models import (
    NormalizedAccountState, NormalizedOrderResult, InstrumentSpec,
)


class ExchangeError(Exception):
    """所有 adapter 内部异常的基类"""


class OrderRejectedError(ExchangeError):
    """下单被交易所拒绝"""


class RateLimitError(ExchangeError):
    """触发交易所限速"""


class InstrumentNotFoundError(ExchangeError):
    """找不到合约规格"""


class ExchangeAdapter(ABC):
    """统一交易所接口。各 adapter 实现此类，服务层只依赖此接口。"""

    # ── 行情 ──────────────────────────────────────────────────────────

    @abstractmethod
    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        """返回 {symbol: mid_price}"""

    @abstractmethod
    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        """返回 {"bids": [[px, sz], ...], "asks": [[px, sz], ...]}"""

    @abstractmethod
    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """返回最近成交列表，每项含 price/size/side/time"""

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> float:
        """返回当前资金费率（小数，如 0.0001）"""

    @abstractmethod
    def get_open_interest(self, symbol: str) -> float:
        """返回未平仓合约量（USD 面值）"""

    # ── 下单 ──────────────────────────────────────────────────────────

    @abstractmethod
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
        """挂限价单。返回归一化结果，内部异常转换为 ExchangeError 子类。"""

    @abstractmethod
    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """撤单。成功返回 True，订单已成交/不存在也返回 True。"""

    @abstractmethod
    def get_order_status(
        self, symbol: str, exchange_order_id: str
    ) -> NormalizedOrderResult:
        """查询订单状态"""

    # ── 账户 ──────────────────────────────────────────────────────────

    @abstractmethod
    def get_account_state(self) -> NormalizedAccountState:
        """返回归一化账户状态（权益、持仓、挂单）"""

    # ── 合约规格 ───────────────────────────────────────────────────────

    @abstractmethod
    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """返回合约规格（最小下单量、价格精度、手续费等）"""
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_models.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/base.py tests/exchange/test_models.py
git commit -m "feat(exchange): add ExchangeAdapter ABC and exception hierarchy"
```

---

### Task 3: 合约规格缓存 InstrumentRegistry

**Files:**
- Create: `shared/exchange/instrument_registry.py`
- Create: `shared/exchange/static_fallback.json`
- Create: `tests/exchange/test_instrument_registry.py`

- [ ] **Step 1: 写静态兜底数据**

```json
// shared/exchange/static_fallback.json
{
  "hyperliquid": {
    "BTC":  {"min_qty": 0.001, "qty_step": 0.001, "price_tick": 0.1,  "contract_size": 1.0, "maker_fee": 0.0002, "taker_fee": 0.0005},
    "ETH":  {"min_qty": 0.01,  "qty_step": 0.01,  "price_tick": 0.01, "contract_size": 1.0, "maker_fee": 0.0002, "taker_fee": 0.0005},
    "SOL":  {"min_qty": 0.1,   "qty_step": 0.1,   "price_tick": 0.01, "contract_size": 1.0, "maker_fee": 0.0002, "taker_fee": 0.0005},
    "ADA":  {"min_qty": 10.0,  "qty_step": 1.0,   "price_tick": 0.0001,"contract_size": 1.0,"maker_fee": 0.0002, "taker_fee": 0.0005},
    "DOGE": {"min_qty": 10.0,  "qty_step": 1.0,   "price_tick": 0.0001,"contract_size": 1.0,"maker_fee": 0.0002, "taker_fee": 0.0005}
  },
  "okx": {
    "BTC":  {"min_qty": 0.01,  "qty_step": 0.01,  "price_tick": 0.1,  "contract_size": 0.01, "maker_fee": 0.0002, "taker_fee": 0.0005},
    "ETH":  {"min_qty": 0.1,   "qty_step": 0.1,   "price_tick": 0.01, "contract_size": 0.1,  "maker_fee": 0.0002, "taker_fee": 0.0005},
    "SOL":  {"min_qty": 1.0,   "qty_step": 1.0,   "price_tick": 0.01, "contract_size": 1.0,  "maker_fee": 0.0002, "taker_fee": 0.0005},
    "ADA":  {"min_qty": 100.0, "qty_step": 1.0,   "price_tick": 0.0001,"contract_size": 10.0,"maker_fee": 0.0002, "taker_fee": 0.0005},
    "DOGE": {"min_qty": 100.0, "qty_step": 1.0,   "price_tick": 0.0001,"contract_size": 10.0,"maker_fee": 0.0002, "taker_fee": 0.0005}
  },
  "binance": {
    "BTC":  {"min_qty": 0.001, "qty_step": 0.001, "price_tick": 0.1,  "contract_size": 1.0, "maker_fee": 0.0002, "taker_fee": 0.0004},
    "ETH":  {"min_qty": 0.001, "qty_step": 0.001, "price_tick": 0.01, "contract_size": 1.0, "maker_fee": 0.0002, "taker_fee": 0.0004},
    "SOL":  {"min_qty": 0.1,   "qty_step": 0.1,   "price_tick": 0.01, "contract_size": 1.0, "maker_fee": 0.0002, "taker_fee": 0.0004},
    "ADA":  {"min_qty": 1.0,   "qty_step": 1.0,   "price_tick": 0.0001,"contract_size": 1.0,"maker_fee": 0.0002, "taker_fee": 0.0004},
    "DOGE": {"min_qty": 1.0,   "qty_step": 1.0,   "price_tick": 0.00001,"contract_size":1.0,"maker_fee": 0.0002, "taker_fee": 0.0004}
  }
}
```

- [ ] **Step 2: 写失败测试（三路径）**

```python
# tests/exchange/test_instrument_registry.py
import json
from unittest.mock import MagicMock, patch
import pytest
from shared.exchange.instrument_registry import InstrumentRegistry
from shared.exchange.models import InstrumentSpec


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.get.return_value = None   # 默认缓存未命中
    return r


def test_fallback_when_redis_miss_and_fetch_fails(mock_redis):
    """Redis 未命中且动态拉取失败时，应返回静态兜底数据"""
    fetch_fn = MagicMock(side_effect=Exception("network error"))
    registry = InstrumentRegistry(redis=mock_redis, exchange="binance")
    spec = registry.get("BTC", fetch_fn=fetch_fn)
    assert isinstance(spec, InstrumentSpec)
    assert spec.symbol == "BTC"
    assert spec.taker_fee == pytest.approx(0.0004)


def test_cache_hit_returns_cached(mock_redis):
    """Redis 命中时直接返回缓存，不调用 fetch"""
    cached = InstrumentSpec("BTC", 0.001, 0.001, 0.1, 1.0, 0.0002, 0.0004)
    mock_redis.get.return_value = json.dumps(cached.__dict__)
    fetch_fn = MagicMock()
    registry = InstrumentRegistry(redis=mock_redis, exchange="binance")
    spec = registry.get("BTC", fetch_fn=fetch_fn)
    fetch_fn.assert_not_called()
    assert spec.symbol == "BTC"


def test_cache_miss_calls_fetch_and_writes_redis(mock_redis):
    """Redis 未命中时调用 fetch_fn，结果写入 Redis"""
    fetched = InstrumentSpec("ETH", 0.001, 0.001, 0.01, 1.0, 0.0002, 0.0004)
    fetch_fn = MagicMock(return_value=fetched)
    registry = InstrumentRegistry(redis=mock_redis, exchange="binance")
    spec = registry.get("ETH", fetch_fn=fetch_fn)
    fetch_fn.assert_called_once_with("ETH")
    mock_redis.setex.assert_called_once()
    assert spec.taker_fee == pytest.approx(0.0004)
```

- [ ] **Step 3: 运行测试，确认失败**

```bash
python -m pytest tests/exchange/test_instrument_registry.py -v 2>&1 | head -20
```

预期：`ImportError`

- [ ] **Step 4: 实现 InstrumentRegistry**

```python
# shared/exchange/instrument_registry.py
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Callable, Optional
from shared.exchange.models import InstrumentSpec

logger = logging.getLogger(__name__)

_FALLBACK_PATH = Path(__file__).parent / "static_fallback.json"
_TTL = 3600  # 秒


def _load_fallback() -> dict:
    with open(_FALLBACK_PATH) as f:
        return json.load(f)


class InstrumentRegistry:
    """
    合约规格缓存。查找顺序：Redis → 动态拉取 → 静态兜底。
    fetch_fn 由各 adapter 提供，签名为 (symbol: str) -> InstrumentSpec。
    """

    def __init__(self, redis, exchange: str):
        self._redis = redis
        self._exchange = exchange
        self._fallback = _load_fallback()

    def _cache_key(self, symbol: str) -> str:
        return f"instrument:{self._exchange}:{symbol}"

    def get(self, symbol: str, fetch_fn: Callable[[str], InstrumentSpec]) -> InstrumentSpec:
        key = self._cache_key(symbol)

        # 1. 尝试 Redis 缓存
        cached = self._redis.get(key)
        if cached:
            try:
                data = json.loads(cached)
                return InstrumentSpec(**data)
            except Exception:
                pass  # 缓存数据损坏，继续往下

        # 2. 动态拉取
        try:
            spec = fetch_fn(symbol)
            self._redis.setex(key, _TTL, json.dumps(spec.__dict__))
            return spec
        except Exception as e:
            logger.warning(f"InstrumentRegistry: fetch failed for {self._exchange}/{symbol}: {e}, using fallback")

        # 3. 静态兜底
        exchange_data = self._fallback.get(self._exchange, {})
        sym_data = exchange_data.get(symbol)
        if sym_data:
            return InstrumentSpec(symbol=symbol, **sym_data)

        from shared.exchange.base import InstrumentNotFoundError
        raise InstrumentNotFoundError(f"No instrument spec found for {self._exchange}/{symbol}")
```

- [ ] **Step 5: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_instrument_registry.py -v
```

预期：全部 PASS

- [ ] **Step 6: 提交**

```bash
git add shared/exchange/instrument_registry.py shared/exchange/static_fallback.json tests/exchange/test_instrument_registry.py
git commit -m "feat(exchange): add InstrumentRegistry with Redis cache + static fallback"
```

---

### Task 4: 工厂函数 factory.py

**Files:**
- Create: `shared/exchange/factory.py`
- Create: `tests/exchange/test_factory.py`

- [ ] **Step 1: 写工厂路由测试**

```python
# tests/exchange/test_factory.py
import os
import pytest
from unittest.mock import patch, MagicMock


def test_factory_selects_hyperliquid(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("HL_PRIVATE_KEY", "0x" + "a" * 64)
    monkeypatch.setenv("HL_ACCOUNT_ADDRESS", "0x" + "b" * 40)
    monkeypatch.setenv("HL_HTTP_URL", "https://api.hyperliquid.xyz")
    with patch("shared.exchange.adapters.hyperliquid.HyperliquidAdapter.__init__", return_value=None):
        from shared.exchange.factory import create_exchange_adapter
        adapter = create_exchange_adapter()
        from shared.exchange.adapters.hyperliquid import HyperliquidAdapter
        assert isinstance(adapter, HyperliquidAdapter)


def test_factory_selects_okx(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "okx")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "pass")
    with patch("shared.exchange.adapters.okx.OKXAdapter.__init__", return_value=None):
        from importlib import reload
        import shared.exchange.factory as fac
        reload(fac)
        adapter = fac.create_exchange_adapter()
        from shared.exchange.adapters.okx import OKXAdapter
        assert isinstance(adapter, OKXAdapter)


def test_factory_selects_binance(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "binance")
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    with patch("shared.exchange.adapters.binance.BinanceAdapter.__init__", return_value=None):
        from importlib import reload
        import shared.exchange.factory as fac
        reload(fac)
        adapter = fac.create_exchange_adapter()
        from shared.exchange.adapters.binance import BinanceAdapter
        assert isinstance(adapter, BinanceAdapter)


def test_factory_raises_for_unknown(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "deribit")
    from importlib import reload
    import shared.exchange.factory as fac
    reload(fac)
    with pytest.raises(ValueError, match="Unsupported exchange"):
        fac.create_exchange_adapter()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/exchange/test_factory.py -v 2>&1 | head -20
```

预期：`ImportError`

- [ ] **Step 3: 实现 factory.py（先用占位 adapter 类）**

```python
# shared/exchange/factory.py
from __future__ import annotations
import os
from shared.exchange.base import ExchangeAdapter


def create_exchange_adapter() -> ExchangeAdapter:
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
```

同时创建三个 adapter 占位文件（让 import 不报错）：

```python
# shared/exchange/adapters/hyperliquid.py  (暂时占位，Task 5 完整实现)
from shared.exchange.base import ExchangeAdapter
class HyperliquidAdapter(ExchangeAdapter):
    def __init__(self, **kwargs): pass
    def get_all_mids(self, symbols): raise NotImplementedError
    def get_l2_book(self, symbol, depth=5): raise NotImplementedError
    def get_recent_trades(self, symbol, limit=50): raise NotImplementedError
    def get_funding_rate(self, symbol): raise NotImplementedError
    def get_open_interest(self, symbol): raise NotImplementedError
    def place_limit(self, symbol, is_buy, qty, limit_px, tif="GTC", reduce_only=False, client_order_id=None): raise NotImplementedError
    def cancel_order(self, symbol, exchange_order_id): raise NotImplementedError
    def get_order_status(self, symbol, exchange_order_id): raise NotImplementedError
    def get_account_state(self): raise NotImplementedError
    def get_instrument_spec(self, symbol): raise NotImplementedError
```

同样创建 okx.py 和 binance.py 占位文件：

```python
# shared/exchange/adapters/okx.py  (暂时占位，Task 11 完整实现)
from shared.exchange.base import ExchangeAdapter
class OKXAdapter(ExchangeAdapter):
    def __init__(self, **kwargs): pass
    def get_all_mids(self, symbols): raise NotImplementedError
    def get_l2_book(self, symbol, depth=5): raise NotImplementedError
    def get_recent_trades(self, symbol, limit=50): raise NotImplementedError
    def get_funding_rate(self, symbol): raise NotImplementedError
    def get_open_interest(self, symbol): raise NotImplementedError
    def place_limit(self, symbol, is_buy, qty, limit_px, tif="GTC", reduce_only=False, client_order_id=None): raise NotImplementedError
    def cancel_order(self, symbol, exchange_order_id): raise NotImplementedError
    def get_order_status(self, symbol, exchange_order_id): raise NotImplementedError
    def get_account_state(self): raise NotImplementedError
    def get_instrument_spec(self, symbol): raise NotImplementedError
```

```python
# shared/exchange/adapters/binance.py  (暂时占位，Task 14 完整实现)
from shared.exchange.base import ExchangeAdapter
class BinanceAdapter(ExchangeAdapter):
    def __init__(self, **kwargs): pass
    def get_all_mids(self, symbols): raise NotImplementedError
    def get_l2_book(self, symbol, depth=5): raise NotImplementedError
    def get_recent_trades(self, symbol, limit=50): raise NotImplementedError
    def get_funding_rate(self, symbol): raise NotImplementedError
    def get_open_interest(self, symbol): raise NotImplementedError
    def place_limit(self, symbol, is_buy, qty, limit_px, tif="GTC", reduce_only=False, client_order_id=None): raise NotImplementedError
    def cancel_order(self, symbol, exchange_order_id): raise NotImplementedError
    def get_order_status(self, symbol, exchange_order_id): raise NotImplementedError
    def get_account_state(self): raise NotImplementedError
    def get_instrument_spec(self, symbol): raise NotImplementedError
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_factory.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/factory.py shared/exchange/adapters/ tests/exchange/test_factory.py
git commit -m "feat(exchange): add factory function with adapter stubs"
```

---

### Task 4b: 更新 shared/exchange/__init__.py 公共导出

**Files:**
- Modify: `shared/exchange/__init__.py`

此步骤在 Chunk 1 完成后立即执行，确保整个 Chunk 1-4 期间都可通过顶层包访问所有类型。

- [ ] **Step 1: 写入公共导出**

```python
# shared/exchange/__init__.py
from shared.exchange.base import (
    ExchangeAdapter, ExchangeError, OrderRejectedError,
    RateLimitError, InstrumentNotFoundError,
)
from shared.exchange.factory import create_exchange_adapter
from shared.exchange.models import (
    NormalizedOrderResult, NormalizedAccountState, NormalizedPosition,
    NormalizedOpenOrder, NormalizedTicker, InstrumentSpec,
)

__all__ = [
    "ExchangeAdapter", "ExchangeError", "OrderRejectedError",
    "RateLimitError", "InstrumentNotFoundError",
    "create_exchange_adapter",
    "NormalizedOrderResult", "NormalizedAccountState", "NormalizedPosition",
    "NormalizedOpenOrder", "NormalizedTicker", "InstrumentSpec",
]
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from shared.exchange import ExchangeAdapter, create_exchange_adapter, NormalizedOrderResult; print('exports OK')"
```

预期：`exports OK`

- [ ] **Step 3: 提交**

```bash
git add shared/exchange/__init__.py
git commit -m "feat(exchange): add public __init__.py exports for exchange package"
```

---

## Chunk 2: HyperliquidAdapter + 服务层迁移（Phase 1）

### Task 5: 实现 HyperliquidAdapter

**Files:**
- Modify: `shared/exchange/adapters/hyperliquid.py`（替换占位实现）
- Create: `tests/exchange/test_hyperliquid_adapter.py`

背景：现有代码中，market_data/portfolio_state 直接用 `httpx` 调用 HL REST API，execution 用 `shared/hl/client.py`。HyperliquidAdapter 整合这两条路径。

- [ ] **Step 1: 写 adapter 方法测试（全部 mock httpx）**

```python
# tests/exchange/test_hyperliquid_adapter.py
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.hyperliquid import HyperliquidAdapter
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter():
    with patch("shared.exchange.adapters.hyperliquid.HyperliquidAdapter._build_sdk_client", return_value=MagicMock()):
        a = HyperliquidAdapter(
            private_key="0x" + "a" * 64,
            account_address="0x" + "b" * 40,
            base_url="https://api.hyperliquid.xyz",
        )
    return a


def test_get_all_mids_returns_float_dict(adapter):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"BTC": "50000.5", "ETH": "3000.1"}
    mock_resp.raise_for_status = MagicMock()
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert result["BTC"] == pytest.approx(50000.5)
    assert result["ETH"] == pytest.approx(3000.1)


def test_place_limit_ack(adapter):
    mock_sdk = MagicMock()
    mock_sdk.order.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"resting": {"oid": 12345}}]}}
    }
    adapter._exchange_sdk = mock_sdk
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, tif="GTC", client_order_id="c1")
    assert isinstance(result, NormalizedOrderResult)
    assert result.status == "ack"
    assert result.exchange_order_id == "12345"


def test_place_limit_dry_run(monkeypatch, adapter):
    monkeypatch.setenv("DRY_RUN", "true")
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, client_order_id="c2")
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_")


def test_cancel_order_success(adapter):
    mock_sdk = MagicMock()
    mock_sdk.cancel.return_value = {"status": "ok"}
    adapter._exchange_sdk = mock_sdk
    assert adapter.cancel_order("BTC", "12345") is True


def test_get_account_state_returns_normalized(adapter):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "marginSummary": {"accountValue": "1000.0", "totalRawUsd": "900.0"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.1", "entryPx": "49000.0",
                          "unrealizedPnl": "100.0"}, "type": "oneWay"}
        ],
    }
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    state = adapter.get_account_state()
    assert isinstance(state, NormalizedAccountState)
    assert state.equity_usd == pytest.approx(1000.0)
    assert "BTC" in state.positions
    assert state.positions["BTC"].qty == pytest.approx(0.1)
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/exchange/test_hyperliquid_adapter.py -v 2>&1 | head -30
```

预期：多个 `NotImplementedError` 或 `AttributeError`

- [ ] **Step 3: 实现 HyperliquidAdapter**

```python
# shared/exchange/adapters/hyperliquid.py
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
    Hyperliquid 永续合约 adapter。
    行情/账户：直接调用 REST /info（与原 market_data/portfolio_state 保持一致）。
    下单/撤单：使用 hyperliquid-python-sdk（需要私钥签名）。

    注意：DRY_RUN 在每次方法调用时读取 os.environ，而非模块导入时，
    确保 monkeypatch.setenv 在测试中生效。
    """

    def __init__(self, private_key: str, account_address: str, base_url: str):
        self._account = account_address
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=10.0)
        self._private_key = private_key  # 保留供 _build_sdk_client 延迟初始化
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

    # ── 行情 ──────────────────────────────────────────────────────────

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

    # ── 下单 ──────────────────────────────────────────────────────────

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

    # ── 账户 ──────────────────────────────────────────────────────────

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

    # ── 合约规格 ───────────────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """动态拉取。由 InstrumentRegistry 缓存，不直接调用此方法。"""
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
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_hyperliquid_adapter.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/adapters/hyperliquid.py tests/exchange/test_hyperliquid_adapter.py
git commit -m "feat(exchange): implement HyperliquidAdapter with full interface"
```

---

### Task 6: 服务层迁移 — market_data

**Files:**
- Modify: `services/market_data/app.py`

注意：market_data 目前直接用 `httpx` 调用 HL REST，需替换为 `ExchangeAdapter` 接口调用。

- [ ] **Step 1: 定位需修改的代码段**

```bash
grep -n "HL_HTTP_URL\|httpx\|allMids\|l2Book\|recentTrades\|metaAndAssetCtxs\|funding\|openInterest" \
  services/market_data/app.py | head -40
```

记录各调用的行号备用。

- [ ] **Step 2: 在 market_data/app.py 顶部替换 import 并初始化 adapter**

找到：
```python
HL_HTTP_URL = os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz")
```

改为（在文件顶部 import 区域后添加）：
```python
from shared.exchange.factory import create_exchange_adapter
_exchange = create_exchange_adapter()
```

保留 `HL_HTTP_URL` 定义以向后兼容（其他地方可能引用），但之后的实际调用全部走 `_exchange`。

- [ ] **Step 3: 替换 allMids 调用**

找到类似：
```python
resp = client.post(f"{HL_HTTP_URL}/info", json={"type": "allMids"})
resp.raise_for_status()
mids = resp.json()
```

替换为：
```python
mids_raw = _exchange.get_all_mids(list(UNIVERSE))
mids = {k: str(v) for k, v in mids_raw.items()}  # 保持字符串格式兼容后续解析
```

- [ ] **Step 4: 替换 metaAndAssetCtxs 调用（funding_rate / open_interest）**

注意：在现有代码（约第 499 行）中，`metaAndAssetCtxs` 的一次调用同时提取了 `funding` 和 `openInterest`，二者共用同一个 API 响应。替换时需要在同一代码块内分别调用两次 adapter：

```python
# 原：一次 metaAndAssetCtxs 调用提取两个字段
# 改为两次 adapter 调用（adapter 内部各自缓存不重复请求）
funding_rate = _exchange.get_funding_rate(symbol)
open_interest = _exchange.get_open_interest(symbol)
```

删除原有 `metaAndAssetCtxs` 响应解析的整个 for 循环块（通常涵盖 funding / openInterest / 借用费等提取逻辑），确保无残留直接 httpx 调用。

- [ ] **Step 5: 替换 l2Book 调用**

找到 `l2Book` 相关代码，替换为：
```python
book = _exchange.get_l2_book(symbol, depth=5)
bids = book["bids"]
asks = book["asks"]
```

- [ ] **Step 6: 替换 recentTrades 调用**

找到 `recentTrades` 相关代码，替换为：
```python
trades = _exchange.get_recent_trades(symbol, limit=50)
```

- [ ] **Step 7: 移除不再使用的 httpx 直接调用和 HL_HTTP_URL 变量**

在确认所有直接 `httpx.Client()` 调用都已替换后，移除对应代码块。保留 `from httpx import ...` 仅在其他非 exchange 用途还需要时保留。

- [ ] **Step 8: 运行已有测试，确认不回退**

```bash
python -m pytest tests/ -v -k "market_data" 2>&1 | tail -20
```

若无对应测试，手动冒烟：
```bash
EXCHANGE=hyperliquid DRY_RUN=true python -c "from services.market_data.app import *; print('import OK')"
```

- [ ] **Step 9: 提交**

```bash
git add services/market_data/app.py
git commit -m "refactor(market_data): replace direct HL httpx calls with ExchangeAdapter"
```

---

### Task 7: 服务层迁移 — execution

**Files:**
- Modify: `services/execution/app.py`

- [ ] **Step 1: 定位现有 HL 调用**

```bash
grep -n "HyperliquidClient\|hl\.place_limit\|hl\.cancel\|shared\.hl" services/execution/app.py
```

- [ ] **Step 2: 替换 import、初始化和 AsyncOrderTracker**

找到并替换（三处）：

**(A) 顶部 import：**
```python
# 删除：
from shared.hl.client import HLConfig, HyperliquidClient
# 添加：
from shared.exchange.factory import create_exchange_adapter
_exchange = create_exchange_adapter()
```

**(B) HyperliquidClient 实例化（约第 763 行）：**
```python
# 删除：
hl = HyperliquidClient(HLConfig(
    private_key=HL_PRIVATE_KEY,
    account_address=HL_ACCOUNT_ADDRESS,
    base_url=HL_HTTP_URL,
))
# 以及 hl_client=hl 参数传入
```
相关类构造函数中 `hl_client` 参数改为接收 `exchange`，`self.hl` 改为 `self.exchange`。

**(C) AsyncOrderTracker 中的双路径迁移：**

`AsyncOrderTracker` 同时持有：
- `self.hl`（用于撤单 cancel）
- `self.http`（用于 `orderStatus` REST 轮询）

两者都需迁移：
```python
# cancel 调用（self.hl.cancel）改为：
self.exchange.cancel_order(symbol, str(oid))

# orderStatus HTTP 轮询改为：
order_result = self.exchange.get_order_status(symbol, str(oid))
# 后续用 order_result.status / order_result.filled_qty 替代原有响应解析
```

删除 `self.http = httpx.Client(...)` 及所有 `order_status(client, HL_HTTP_URL, ...)` 函数调用，改为 `self.exchange.get_order_status()`。

- [ ] **Step 3: 替换 place_limit 调用（含参数名和响应解析）**

找到实际调用（约第 1246 行），现有签名为：
```python
res = hl.place_limit(coin=s.symbol, is_buy=is_buy, sz=float(qty),
                     limit_px=float(limit_px), tif="Gtc", reduce_only=...)
```

替换为（注意参数名和 tif 格式变化）：
```python
res = _exchange.place_limit(
    symbol=s.symbol,      # coin → symbol
    is_buy=is_buy,
    qty=float(qty),       # sz → qty
    limit_px=float(limit_px),
    tif="GTC",            # "Gtc" → "GTC"（adapter 内部做映射）
    reduce_only=reduce_only,
    client_order_id=client_order_id,
)
```

**关键：** 删除紧跟其后的 HL SDK 响应解析块（通常约 20-40 行，解析 `res.get("response").get("data").get("statuses")` 等原始字典结构），改为直接使用归一化字段：
```python
exchange_order_id = res.exchange_order_id  # 原: oid = statuses[0]["resting"]["oid"]
status = res.status                        # 原: "filled" if "filled" in s else "resting"
filled_qty = res.filled_qty                # 原: float(s.get("filled", {}).get("totalSz", 0))
avg_px = res.avg_px                        # 原: float(s.get("filled", {}).get("avgPx", 0))
```

- [ ] **Step 4: 替换 cancel 调用**

找到：
```python
self.hl.cancel(symbol, oid)
```

替换为：
```python
_exchange.cancel_order(symbol, str(oid))
```

- [ ] **Step 5: 运行 execution 相关测试**

```bash
python -m pytest tests/ -v -k "execution" 2>&1 | tail -20
```

- [ ] **Step 6: 提交**

```bash
git add services/execution/app.py
git commit -m "refactor(execution): replace HyperliquidClient with ExchangeAdapter"
```

---

### Task 8: 服务层迁移 — portfolio_state

**Files:**
- Modify: `services/portfolio_state/app.py`

- [ ] **Step 1: 定位现有 HL 调用**

```bash
grep -n "HL_HTTP_URL\|httpx\|clearinghouseState\|openOrders\|orderStatus\|allMids" \
  services/portfolio_state/app.py
```

- [ ] **Step 2: 替换 import 和初始化**

在文件顶部添加：
```python
from shared.exchange.factory import create_exchange_adapter
_exchange = create_exchange_adapter()
```

- [ ] **Step 3: 替换 clearinghouseState 调用（对账）**

找到类似：
```python
resp = client.post(f"{HL_HTTP_URL}/info", json={"type": "clearinghouseState", "user": ACCOUNT})
```

替换为：
```python
state = _exchange.get_account_state()
# state 是 NormalizedAccountState，字段访问改用 state.equity_usd, state.positions 等
```

调整后续依赖 HL 原始格式的解析逻辑，使用归一化字段。

- [ ] **Step 4: 替换 orderStatus 调用**

找到：
```python
stj = order_status(client, HL_HTTP_URL, ACCOUNT, oid)
```

替换为：
```python
order_result = _exchange.get_order_status(symbol, str(oid))
```

- [ ] **Step 5: 运行 portfolio_state 相关测试**

```bash
python -m pytest tests/ -v -k "portfolio" 2>&1 | tail -20
```

- [ ] **Step 6: 完整回归测试**

```bash
EXCHANGE=hyperliquid python -m pytest tests/ -v 2>&1 | tail -30
```

全部 PASS 后提交。

- [ ] **Step 7: 提交**

```bash
git add services/portfolio_state/app.py
git commit -m "refactor(portfolio_state): replace direct HL httpx calls with ExchangeAdapter"
```

---

### Task 9: 配置变更 — .env.example 和 trading_params.json

**Files:**
- Modify: `infra/.env.example`
- Modify: `config/trading_params.json`
- Modify: `services/ai_decision/config_loader.py`

- [ ] **Step 1: 更新 .env.example**

在文件末尾追加：

```bash
# ── 交易所选择 ─────────────────────────────────
EXCHANGE=hyperliquid   # hyperliquid | okx | binance

# ── OKX 凭证（EXCHANGE=okx 时填写）─────────────
OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=
OKX_HTTP_URL=https://www.okx.com

# ── Binance 凭证（EXCHANGE=binance 时填写）───────
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_FAPI_URL=https://fapi.binance.com
```

- [ ] **Step 2: 更新 trading_params.json**

在根对象中新增 `exchange_overrides` 块（在 `active_version` 之后）：

```json
"exchange_overrides": {
  "okx": {
    "MIN_NOTIONAL_USD": 5.0,
    "SLICES": 3,
    "_note": "OKX 合约面值以张计，最小成交约5U"
  },
  "binance": {
    "MIN_NOTIONAL_USD": 5.0,
    "SLICES": 3,
    "_note": "Binance FAPI 最小名义价值5U"
  }
}
```

- [ ] **Step 3: 更新 config_loader.py — 合并 exchange_overrides**

找到 `load_config()` 函数，在返回 params 前追加合并逻辑：

```python
# 合并 exchange_overrides（如果有）
exchange = os.environ.get("EXCHANGE", "hyperliquid").lower()
overrides = raw.get("exchange_overrides", {}).get(exchange, {})
for k, v in overrides.items():
    if not k.startswith("_"):   # 跳过注释字段
        params[k] = v
```

- [ ] **Step 4: 为 config_loader 新增覆盖测试**

```python
# 在现有 tests/test_config_loader.py 中追加
def test_exchange_overrides_applied(tmp_path, monkeypatch):
    config = {
        "active_version": "V9",
        "exchange_overrides": {
            "okx": {"MIN_NOTIONAL_USD": 5.0}
        },
        "versions": {
            "V9": {**REQUIRED_TEST_PARAMS, "MIN_NOTIONAL_USD": 50.0}
        }
    }
    p = tmp_path / "params.json"
    p.write_text(json.dumps(config))
    monkeypatch.setenv("EXCHANGE", "okx")
    result = load_config(str(p))
    assert result["MIN_NOTIONAL_USD"] == 5.0  # override 生效


def test_exchange_overrides_skips_notes(tmp_path, monkeypatch):
    config = {
        "active_version": "V9",
        "exchange_overrides": {
            "binance": {"_note": "should be skipped", "MIN_NOTIONAL_USD": 5.0}
        },
        "versions": {"V9": {**REQUIRED_TEST_PARAMS}}
    }
    p = tmp_path / "params.json"
    p.write_text(json.dumps(config))
    monkeypatch.setenv("EXCHANGE", "binance")
    result = load_config(str(p))
    assert "_note" not in result
```

- [ ] **Step 5: 运行配置测试**

```bash
python -m pytest tests/test_config_loader.py -v
```

预期：全部 PASS

- [ ] **Step 6: Phase 1 完整回归**

```bash
EXCHANGE=hyperliquid python -m pytest tests/ -v 2>&1 | tail -20
```

确认全部通过，Phase 1 完成。

- [ ] **Step 7: 提交**

```bash
git add infra/.env.example config/trading_params.json services/ai_decision/config_loader.py tests/test_config_loader.py
git commit -m "feat(config): add EXCHANGE env var, exchange_overrides in trading_params.json"
```

---

## Chunk 3: OKXAdapter（Phase 2）

### Task 10: 安装 OKX SDK

- [ ] **Step 1: 添加依赖**

```bash
# 如果用 Poetry
poetry add python-okx

# 如果用 pip / pyproject.toml
# 在 pyproject.toml [project.dependencies] 添加 "python-okx>=0.2"
pip install python-okx
```

- [ ] **Step 2: 确认导入成功**

```bash
python -c "from okx import Trade, MarketData, Account, PublicData; print('okx SDK ok')"
```

- [ ] **Step 3: 提交依赖变更**

```bash
git add pyproject.toml poetry.lock  # 或 requirements.txt
git commit -m "chore(deps): add python-okx SDK"
```

---

### Task 11: 实现 OKXAdapter

**Files:**
- Modify: `shared/exchange/adapters/okx.py`（替换占位实现）
- Create: `tests/exchange/test_okx_adapter.py`

- [ ] **Step 1: 写 OKXAdapter 测试**

```python
# tests/exchange/test_okx_adapter.py
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.okx import OKXAdapter
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter(monkeypatch):
    # mock OKX SDK 子客户端的 __init__，阻止真实网络初始化
    with patch("okx.Trade.TradeAPI.__init__", return_value=None), \
         patch("okx.MarketData.MarketAPI.__init__", return_value=None), \
         patch("okx.Account.AccountAPI.__init__", return_value=None), \
         patch("okx.PublicData.PublicAPI.__init__", return_value=None):
        a = OKXAdapter(api_key="k", api_secret="s", passphrase="p",
                       base_url="https://www.okx.com")
    # 手动注入 mock 子客户端（__init__ 被 mock 后属性未赋值）
    a._trade = MagicMock()
    a._market = MagicMock()
    a._account = MagicMock()
    a._public = MagicMock()
    return a


def test_symbol_to_inst_id():
    assert OKXAdapter._to_inst_id("BTC") == "BTC-USDT-SWAP"
    assert OKXAdapter._to_inst_id("ETH") == "ETH-USDT-SWAP"


def test_get_all_mids(adapter):
    mock_resp = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "bidPx": "49999", "askPx": "50001"},
        {"instId": "ETH-USDT-SWAP", "bidPx": "2999", "askPx": "3001"},
    ]}
    adapter._market.get_tickers = MagicMock(return_value=mock_resp)
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert result["BTC"] == pytest.approx(50000.0)
    assert result["ETH"] == pytest.approx(3000.0)


def test_place_limit_ack(adapter, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    mock_resp = {"code": "0", "data": [{"ordId": "99999", "clOrdId": "c1", "sCode": "0"}]}
    adapter._trade.place_order = MagicMock(return_value=mock_resp)
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, client_order_id="c1")
    assert isinstance(result, NormalizedOrderResult)
    assert result.exchange_order_id == "99999"
    assert result.status == "ack"


def test_place_limit_dry_run(adapter, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, client_order_id="c2")
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_")


def test_qty_to_contracts_btc(adapter):
    """BTC contract_size=0.01 BTC/张，0.1 BTC = 10张"""
    from shared.exchange.models import InstrumentSpec
    spec = InstrumentSpec("BTC", 0.01, 0.01, 0.1, 0.01, 0.0002, 0.0005)
    contracts = adapter._qty_to_contracts("BTC", 0.1, spec)
    assert contracts == "10"


def test_get_account_state(adapter):
    adapter._account.get_account_balance = MagicMock(return_value={
        "code": "0", "data": [{"totalEq": "1000.0", "details": [
            {"ccy": "USDT", "availBal": "900.0"}
        ]}]
    })
    adapter._account.get_positions = MagicMock(return_value={
        "code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "pos": "10", "avgPx": "50000",
             "markPx": "51000", "upl": "100", "posSide": "long"}
        ]
    })
    state = adapter.get_account_state()
    assert isinstance(state, NormalizedAccountState)
    assert state.equity_usd == pytest.approx(1000.0)
    assert "BTC" in state.positions
    assert state.positions["BTC"].side == "long"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/exchange/test_okx_adapter.py -v 2>&1 | head -20
```

- [ ] **Step 3: 实现 OKXAdapter**

```python
# shared/exchange/adapters/okx.py
from __future__ import annotations
import os
import time
import logging
import math
from typing import Optional
from shared.exchange.base import ExchangeAdapter, ExchangeError, OrderRejectedError, RateLimitError
from shared.exchange.models import (
    NormalizedOrderResult, NormalizedAccountState, NormalizedPosition,
    NormalizedOpenOrder, InstrumentSpec,
)

logger = logging.getLogger(__name__)

# OKX 内部符号 → 统一符号
_INST_TO_SYMBOL = {}  # 动态反查用
_SYMBOL_TO_INST = {}  # 延迟初始化


class OKXAdapter(ExchangeAdapter):
    """
    OKX 永续合约 adapter。
    使用 python-okx 官方 SDK。合约类型固定为 SWAP（USDT保证金）。

    模拟盘切换：设置环境变量 OKX_SIMULATED=true，SDK 将使用 flag="1"。
    注意：DRY_RUN 在每次方法调用时读取 os.environ，而非模块导入时。
    """

    # 静态符号映射（覆盖 UNIVERSE 中的 5 个主流币）
    _SYMBOL_MAP: dict[str, str] = {
        "BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP",
        "SOL": "SOL-USDT-SWAP", "ADA": "ADA-USDT-SWAP",
        "DOGE": "DOGE-USDT-SWAP",
    }

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    @staticmethod
    def _to_inst_id(symbol: str) -> str:
        return OKXAdapter._SYMBOL_MAP.get(symbol, f"{symbol}-USDT-SWAP")

    @staticmethod
    def _from_inst_id(inst_id: str) -> str:
        return inst_id.replace("-USDT-SWAP", "")

    def __init__(self, api_key: str, api_secret: str, passphrase: str, base_url: str):
        # flag="0" 实盘, flag="1" 模拟盘（OKX 模拟盘通过 flag 切换，不是 URL）
        flag = "1" if os.environ.get("OKX_SIMULATED", "false").lower() == "true" else "0"
        try:
            from okx import Trade, MarketData, Account, PublicData
            self._trade = Trade.TradeAPI(api_key, api_secret, passphrase, False, flag)
            self._market = MarketData.MarketAPI(flag=flag)
            self._account = Account.AccountAPI(api_key, api_secret, passphrase, False, flag)
            self._public = PublicData.PublicAPI(flag=flag)
        except ImportError as e:
            raise RuntimeError("python-okx not installed. Run: pip install python-okx") from e

    def _check(self, resp: dict, context: str = "") -> dict:
        if resp.get("code") != "0":
            msg = resp.get("msg", str(resp))
            raise ExchangeError(f"OKX {context} error: {msg}")
        return resp

    def _qty_to_contracts(self, symbol: str, qty: float, spec: InstrumentSpec) -> str:
        """将 base asset 数量转换为 OKX 张数"""
        contracts = qty / spec.contract_size
        return str(int(math.floor(contracts)))

    # ── 行情 ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        resp = self._check(self._market.get_tickers(instType="SWAP"), "get_tickers")
        result = {}
        for item in resp.get("data", []):
            inst_id = item["instId"]
            sym = self._from_inst_id(inst_id)
            if sym in symbols:
                mid = (float(item["bidPx"]) + float(item["askPx"])) / 2
                result[sym] = mid
        return result

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        inst_id = self._to_inst_id(symbol)
        resp = self._check(self._market.get_orderbook(instId=inst_id, sz=str(depth)), "get_orderbook")
        data = resp["data"][0] if resp.get("data") else {}
        return {"bids": data.get("bids", [])[:depth], "asks": data.get("asks", [])[:depth]}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        inst_id = self._to_inst_id(symbol)
        resp = self._check(self._market.get_trades(instId=inst_id, limit=str(limit)), "get_trades")
        return [{"price": float(t["px"]), "size": float(t["sz"]),
                 "side": t["side"], "time": t["ts"]}
                for t in resp.get("data", [])]

    def get_funding_rate(self, symbol: str) -> float:
        inst_id = self._to_inst_id(symbol)
        resp = self._check(self._public.get_funding_rate(instId=inst_id), "get_funding_rate")
        data = resp.get("data", [{}])
        return float(data[0].get("fundingRate", 0)) if data else 0.0

    def get_open_interest(self, symbol: str) -> float:
        inst_id = self._to_inst_id(symbol)
        resp = self._check(self._public.get_open_interest(instType="SWAP", instId=inst_id), "get_oi")
        data = resp.get("data", [{}])
        return float(data[0].get("oiUsd", 0)) if data else 0.0

    # ── 下单 ──────────────────────────────────────────────────────────

    def place_limit(
        self, symbol: str, is_buy: bool, qty: float, limit_px: float,
        tif: str = "GTC", reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> NormalizedOrderResult:
        t0 = time.monotonic()
        cid = client_order_id or f"okx_{int(time.time()*1000)}"

        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id=cid, exchange_order_id=f"dry_{cid}",
                status="ack", filled_qty=0.0, avg_px=0.0, fee_usd=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        inst_id = self._to_inst_id(symbol)
        side = "buy" if is_buy else "sell"
        # OKX 双向持仓模式：做多用 long，做空用 short
        pos_side = "long" if is_buy else "short"
        if reduce_only:
            pos_side = "short" if is_buy else "long"

        # 获取合约规格（用于数量转换）
        from shared.exchange.instrument_registry import InstrumentRegistry
        # 简化：直接读 static_fallback（正式场景由 InstrumentRegistry 提供）
        spec = self.get_instrument_spec(symbol)
        sz = self._qty_to_contracts(symbol, qty, spec)

        tif_map = {"GTC": "GTC", "IOC": "IOC", "ALO": "post_only"}
        resp = self._trade.place_order(
            instId=inst_id, tdMode="cross", side=side, posSide=pos_side,
            ordType="limit", sz=sz, px=str(limit_px),
            clOrdId=cid, tgtCcy="base_ccy",
        )
        self._check(resp, "place_order")
        data = resp["data"][0]
        if data.get("sCode") != "0":
            raise OrderRejectedError(f"OKX order rejected: {data.get('sMsg')}")

        return NormalizedOrderResult(
            client_order_id=cid, exchange_order_id=data["ordId"],
            status="ack", filled_qty=0.0, avg_px=0.0, fee_usd=0.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        if self._is_dry_run():
            return True
        inst_id = self._to_inst_id(symbol)
        resp = self._trade.cancel_order(instId=inst_id, ordId=exchange_order_id)
        return resp.get("code") == "0"

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        inst_id = self._to_inst_id(symbol)
        resp = self._check(self._trade.get_order(instId=inst_id, ordId=exchange_order_id), "get_order")
        data = resp["data"][0]
        status_map = {"live": "ack", "partially_filled": "ack",
                      "filled": "filled", "canceled": "canceled"}
        return NormalizedOrderResult(
            client_order_id=data.get("clOrdId", ""),
            exchange_order_id=exchange_order_id,
            status=status_map.get(data.get("state", ""), "ack"),
            filled_qty=float(data.get("fillSz", 0)),
            avg_px=float(data.get("avgPx") or 0),
            fee_usd=abs(float(data.get("fee", 0))),
            latency_ms=0,
        )

    # ── 账户 ──────────────────────────────────────────────────────────

    def get_account_state(self) -> NormalizedAccountState:
        bal_resp = self._check(self._account.get_account_balance(), "get_balance")
        bal_data = bal_resp["data"][0] if bal_resp.get("data") else {}
        equity = float(bal_data.get("totalEq", 0))
        cash = float((bal_data.get("details") or [{}])[0].get("availBal", 0))

        pos_resp = self._check(self._account.get_positions(instType="SWAP"), "get_positions")
        positions = {}
        for p in pos_resp.get("data", []):
            sym = self._from_inst_id(p.get("instId", ""))
            qty_contracts = float(p.get("pos", 0))
            if qty_contracts == 0:
                continue
            spec = self.get_instrument_spec(sym)
            qty_base = qty_contracts * spec.contract_size
            pos_side = p.get("posSide", "long")
            if pos_side == "short":
                qty_base = -qty_base
            positions[sym] = NormalizedPosition(
                symbol=sym, qty=qty_base,
                entry_px=float(p.get("avgPx") or 0),
                mark_px=float(p.get("markPx") or 0),
                unreal_pnl=float(p.get("upl", 0)),
                side=pos_side,
            )

        return NormalizedAccountState(equity_usd=equity, cash_usd=cash,
                                      positions=positions, open_orders=[])

    # ── 合约规格 ───────────────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        inst_id = self._to_inst_id(symbol)
        resp = self._public.get_instruments(instType="SWAP", instId=inst_id)
        if resp.get("code") == "0" and resp.get("data"):
            d = resp["data"][0]
            ct_val = float(d.get("ctVal", 1))      # 合约面值（BTC张=0.01）
            lot_sz = float(d.get("lotSz", ct_val)) # 最小下单张数
            tick_sz = float(d.get("tickSz", 0.01))
            return InstrumentSpec(
                symbol=symbol, min_qty=lot_sz * ct_val, qty_step=lot_sz * ct_val,
                price_tick=tick_sz, contract_size=ct_val,
                maker_fee=0.0002, taker_fee=0.0005,
            )
        # 兜底
        from shared.exchange.instrument_registry import _load_fallback
        fb = _load_fallback().get("okx", {}).get(symbol, {})
        if fb:
            return InstrumentSpec(symbol=symbol, **fb)
        raise ValueError(f"OKX instrument spec not found for {symbol}")
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_okx_adapter.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/adapters/okx.py tests/exchange/test_okx_adapter.py
git commit -m "feat(exchange): implement OKXAdapter for SWAP perpetuals"
```

---

### Task 12: OKX DRY_RUN 冒烟测试

- [ ] **Step 1: 设置环境变量并运行冒烟**

```bash
export EXCHANGE=okx
export OKX_API_KEY=placeholder
export OKX_API_SECRET=placeholder
export OKX_PASSPHRASE=placeholder
export DRY_RUN=true
export REDIS_URL=redis://127.0.0.1:6379/0

python -c "
from shared.exchange.factory import create_exchange_adapter
ex = create_exchange_adapter()
print('adapter type:', type(ex).__name__)
res = ex.place_limit('BTC', True, 0.01, 50000.0, client_order_id='smoke_test')
print('place_limit result:', res)
print('SMOKE OK')
"
```

预期输出：
```
adapter type: OKXAdapter
place_limit result: NormalizedOrderResult(...status='ack', exchange_order_id='dry_smoke_test'...)
SMOKE OK
```

- [ ] **Step 2: 若有 OKX 模拟盘凭证，进行真实 API 测试**

OKX 模拟盘通过 SDK `flag="1"` 切换，URL 仍为 `https://www.okx.com`（生产地址），不是不同的 URL。

```bash
export DRY_RUN=false
export OKX_SIMULATED=true        # 触发 OKXAdapter.__init__ 中 flag="1"
export OKX_API_KEY=<模拟盘 key>
export OKX_API_SECRET=<模拟盘 secret>
export OKX_PASSPHRASE=<模拟盘 passphrase>

python -c "
from shared.exchange.factory import create_exchange_adapter
ex = create_exchange_adapter()
mids = ex.get_all_mids(['BTC', 'ETH'])
print('mids:', mids)
state = ex.get_account_state()
print('equity:', state.equity_usd)
"
```

- [ ] **Step 3: 提交（若有代码修复）**

```bash
git add -p
git commit -m "fix(okx): address issues found in smoke test" # 如有修复
```

---

## Chunk 4: BinanceAdapter（Phase 3）

### Task 13: 安装 Binance SDK

- [ ] **Step 1: 添加依赖**

```bash
pip install binance-futures-connector
# 或 poetry add binance-futures-connector
```

- [ ] **Step 2: 确认导入**

```bash
python -c "from binance.um_futures import UMFutures; print('binance FAPI SDK ok')"
```

- [ ] **Step 3: 提交依赖**

```bash
git add pyproject.toml poetry.lock
git commit -m "chore(deps): add binance-futures-connector SDK"
```

---

### Task 14: 实现 BinanceAdapter

**Files:**
- Modify: `shared/exchange/adapters/binance.py`（替换占位）
- Create: `tests/exchange/test_binance_adapter.py`

- [ ] **Step 1: 写 BinanceAdapter 测试**

```python
# tests/exchange/test_binance_adapter.py
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.binance import BinanceAdapter
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    with patch("binance.um_futures.UMFutures.__init__", return_value=None):
        a = BinanceAdapter(api_key="k", api_secret="s",
                           base_url="https://fapi.binance.com")
    a._client = MagicMock()
    return a


def test_symbol_to_binance():
    assert BinanceAdapter._to_binance_sym("BTC") == "BTCUSDT"
    assert BinanceAdapter._to_binance_sym("DOGE") == "DOGEUSDT"


def test_get_all_mids(adapter):
    adapter._client.book_ticker.side_effect = lambda symbol: {
        "BTCUSDT": {"bidPrice": "49999", "askPrice": "50001"},
        "ETHUSDT": {"bidPrice": "2999", "askPrice": "3001"},
    }[symbol]
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert result["BTC"] == pytest.approx(50000.0)
    assert result["ETH"] == pytest.approx(3000.0)


def test_place_limit_ack(adapter, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    adapter._client.new_order.return_value = {
        "orderId": 88888, "clientOrderId": "c1",
        "status": "NEW", "executedQty": "0", "avgPrice": "0"
    }
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, client_order_id="c1")
    assert isinstance(result, NormalizedOrderResult)
    assert result.exchange_order_id == "88888"
    assert result.status == "ack"


def test_place_limit_dry_run(adapter, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    result = adapter.place_limit("ETH", False, 0.1, 3000.0, client_order_id="c3")
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_")


def test_get_account_state(adapter):
    adapter._client.account.return_value = {
        "totalWalletBalance": "1000.0",
        "availableBalance": "900.0",
        "positions": [
            {"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "50000",
             "markPrice": "51000", "unrealizedProfit": "10", "positionSide": "LONG"}
        ]
    }
    state = adapter.get_account_state()
    assert isinstance(state, NormalizedAccountState)
    assert state.equity_usd == pytest.approx(1000.0)
    assert "BTC" in state.positions
    assert state.positions["BTC"].side == "long"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/exchange/test_binance_adapter.py -v 2>&1 | head -20
```

- [ ] **Step 3: 实现 BinanceAdapter**

```python
# shared/exchange/adapters/binance.py
from __future__ import annotations
import os
import time
import logging
from typing import Optional
from shared.exchange.base import ExchangeAdapter, ExchangeError, OrderRejectedError
from shared.exchange.models import (
    NormalizedOrderResult, NormalizedAccountState, NormalizedPosition,
    NormalizedOpenOrder, InstrumentSpec,
)

logger = logging.getLogger(__name__)


class BinanceAdapter(ExchangeAdapter):
    """
    Binance FAPI（合约）adapter。
    使用 binance-futures-connector 官方 SDK。
    要求账户开启双向持仓模式（Hedge Mode）。

    注意：DRY_RUN 在每次方法调用时读取 os.environ，而非模块导入时。
    """

    @staticmethod
    def _is_dry_run() -> bool:
        return os.environ.get("DRY_RUN", "true").lower() == "true"

    _SYMBOL_MAP: dict[str, str] = {
        "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
        "ADA": "ADAUSDT", "DOGE": "DOGEUSDT",
    }

    @staticmethod
    def _to_binance_sym(symbol: str) -> str:
        return BinanceAdapter._SYMBOL_MAP.get(symbol, f"{symbol}USDT")

    @staticmethod
    def _from_binance_sym(binance_sym: str) -> str:
        for k, v in BinanceAdapter._SYMBOL_MAP.items():
            if v == binance_sym:
                return k
        return binance_sym.replace("USDT", "")

    def __init__(self, api_key: str, api_secret: str, base_url: str):
        try:
            from binance.um_futures import UMFutures
            self._client = UMFutures(key=api_key, secret=api_secret, base_url=base_url)
        except ImportError as e:
            raise RuntimeError("binance-futures-connector not installed. Run: pip install binance-futures-connector") from e

    # ── 行情 ──────────────────────────────────────────────────────────

    def get_all_mids(self, symbols: list[str]) -> dict[str, float]:
        result = {}
        for sym in symbols:
            try:
                ticker = self._client.book_ticker(symbol=self._to_binance_sym(sym))
                mid = (float(ticker["bidPrice"]) + float(ticker["askPrice"])) / 2
                result[sym] = mid
            except Exception as e:
                logger.warning(f"Binance get_all_mids failed for {sym}: {e}")
        return result

    def get_l2_book(self, symbol: str, depth: int = 5) -> dict:
        data = self._client.depth(symbol=self._to_binance_sym(symbol), limit=depth)
        return {"bids": data.get("bids", [])[:depth], "asks": data.get("asks", [])[:depth]}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        trades = self._client.trades(symbol=self._to_binance_sym(symbol), limit=limit)
        return [{"price": float(t["price"]), "size": float(t["qty"]),
                 "side": "buy" if t["isBuyerMaker"] else "sell",
                 "time": t["time"]}
                for t in trades]

    def get_funding_rate(self, symbol: str) -> float:
        data = self._client.mark_price(symbol=self._to_binance_sym(symbol))
        return float(data.get("lastFundingRate", 0))

    def get_open_interest(self, symbol: str) -> float:
        data = self._client.open_interest(symbol=self._to_binance_sym(symbol))
        return float(data.get("openInterest", 0))

    # ── 下单 ──────────────────────────────────────────────────────────

    def place_limit(
        self, symbol: str, is_buy: bool, qty: float, limit_px: float,
        tif: str = "GTC", reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> NormalizedOrderResult:
        t0 = time.monotonic()
        cid = client_order_id or f"bnb_{int(time.time()*1000)}"

        if self._is_dry_run():
            return NormalizedOrderResult(
                client_order_id=cid, exchange_order_id=f"dry_{cid}",
                status="ack", filled_qty=0.0, avg_px=0.0, fee_usd=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        side = "BUY" if is_buy else "SELL"
        # Binance Hedge Mode: BUY + LONG 开多，SELL + SHORT 开空
        pos_side = "LONG" if is_buy else "SHORT"
        if reduce_only:
            pos_side = "SHORT" if is_buy else "LONG"

        tif_map = {"GTC": "GTC", "IOC": "IOC", "ALO": "GTX"}
        try:
            resp = self._client.new_order(
                symbol=self._to_binance_sym(symbol),
                side=side, positionSide=pos_side,
                type="LIMIT", timeInForce=tif_map.get(tif, "GTC"),
                quantity=str(qty), price=str(limit_px),
                newClientOrderId=cid,
            )
        except Exception as e:
            raise OrderRejectedError(str(e)) from e

        status_map = {"NEW": "ack", "PARTIALLY_FILLED": "ack",
                      "FILLED": "filled", "CANCELED": "canceled", "REJECTED": "rejected"}
        return NormalizedOrderResult(
            client_order_id=cid,
            exchange_order_id=str(resp["orderId"]),
            status=status_map.get(resp.get("status", "NEW"), "ack"),
            filled_qty=float(resp.get("executedQty", 0)),
            avg_px=float(resp.get("avgPrice") or 0),
            fee_usd=0.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        if self._is_dry_run():
            return True
        try:
            self._client.cancel_order(
                symbol=self._to_binance_sym(symbol), orderId=int(exchange_order_id)
            )
            return True
        except Exception as e:
            logger.warning(f"cancel_order failed: {e}")
            return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> NormalizedOrderResult:
        resp = self._client.query_order(
            symbol=self._to_binance_sym(symbol), orderId=int(exchange_order_id)
        )
        status_map = {"NEW": "ack", "PARTIALLY_FILLED": "ack",
                      "FILLED": "filled", "CANCELED": "canceled", "REJECTED": "rejected"}
        return NormalizedOrderResult(
            client_order_id=resp.get("clientOrderId", ""),
            exchange_order_id=exchange_order_id,
            status=status_map.get(resp.get("status", ""), "ack"),
            filled_qty=float(resp.get("executedQty", 0)),
            avg_px=float(resp.get("avgPrice") or 0),
            fee_usd=0.0, latency_ms=0,
        )

    # ── 账户 ──────────────────────────────────────────────────────────

    def get_account_state(self) -> NormalizedAccountState:
        data = self._client.account()
        equity = float(data.get("totalWalletBalance", 0))
        cash = float(data.get("availableBalance", 0))

        positions = {}
        for p in data.get("positions", []):
            qty = float(p.get("positionAmt", 0))
            if qty == 0:
                continue
            sym = self._from_binance_sym(p.get("symbol", ""))
            pos_side = p.get("positionSide", "BOTH")
            positions[sym] = NormalizedPosition(
                symbol=sym, qty=qty,
                entry_px=float(p.get("entryPrice") or 0),
                mark_px=float(p.get("markPrice") or 0),
                unreal_pnl=float(p.get("unrealizedProfit", 0)),
                side=pos_side.lower(),
            )

        return NormalizedAccountState(equity_usd=equity, cash_usd=cash,
                                      positions=positions, open_orders=[])

    # ── 合约规格 ───────────────────────────────────────────────────────

    def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        binance_sym = self._to_binance_sym(symbol)
        info = self._client.exchange_info()
        for sym_info in info.get("symbols", []):
            if sym_info["symbol"] == binance_sym:
                lot_filter = next(
                    (f for f in sym_info.get("filters", []) if f["filterType"] == "LOT_SIZE"), {}
                )
                price_filter = next(
                    (f for f in sym_info.get("filters", []) if f["filterType"] == "PRICE_FILTER"), {}
                )
                min_qty = float(lot_filter.get("minQty", 0.001))
                step = float(lot_filter.get("stepSize", 0.001))
                tick = float(price_filter.get("tickSize", 0.01))
                return InstrumentSpec(
                    symbol=symbol, min_qty=min_qty, qty_step=step,
                    price_tick=tick, contract_size=1.0,
                    maker_fee=0.0002, taker_fee=0.0004,
                )
        # 兜底
        from shared.exchange.instrument_registry import _load_fallback
        fb = _load_fallback().get("binance", {}).get(symbol, {})
        if fb:
            return InstrumentSpec(symbol=symbol, **fb)
        raise ValueError(f"Binance instrument spec not found for {symbol}")
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/exchange/test_binance_adapter.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/adapters/binance.py tests/exchange/test_binance_adapter.py
git commit -m "feat(exchange): implement BinanceAdapter for FAPI perpetuals"
```

---

### Task 15: Binance DRY_RUN 冒烟测试 + 全量回归

- [ ] **Step 1: DRY_RUN 冒烟**

```bash
export EXCHANGE=binance
export BINANCE_API_KEY=placeholder
export BINANCE_API_SECRET=placeholder
export DRY_RUN=true

python -c "
from shared.exchange.factory import create_exchange_adapter
ex = create_exchange_adapter()
print('adapter type:', type(ex).__name__)
res = ex.place_limit('ETH', False, 0.1, 3000.0, client_order_id='bnb_smoke')
print('place_limit result:', res)
print('SMOKE OK')
"
```

- [ ] **Step 2: exchange adapter 专项回归（按交易所隔离）**

注意：EXCHANGE 环境变量会影响 factory 的行为，因此用 `tests/exchange/` 而非 `tests/` 避免干扰服务层测试：

```bash
# adapter 单元测试（各自 mock，不依赖真实凭证）
python -m pytest tests/exchange/ -v 2>&1 | tail -15

# 服务层回归（固定 EXCHANGE=hyperliquid）
EXCHANGE=hyperliquid python -m pytest tests/ -v --ignore=tests/exchange 2>&1 | tail -10
```

全部 PASS 后视为实现完成。

- [ ] **Step 3: 最终提交**

```bash
git commit -m "feat(exchange): finalize multi-exchange adapter layer (HL + OKX + Binance)" --allow-empty
```
