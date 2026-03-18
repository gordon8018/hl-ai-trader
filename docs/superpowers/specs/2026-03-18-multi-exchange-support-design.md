# 多交易所支持设计文档

**日期**: 2026-03-18
**状态**: 已确认
**目标**: 在现有 Hyperliquid 基础上，增加对 OKX 和 Binance 永续合约的支持

---

## 背景与目标

当前系统仅支持 Hyperliquid 交易所，交易所调用直接散布在三个服务中（market_data、execution、portfolio_state）。本方案通过引入 **Thin Adapter 模式**，建立统一的交易所抽象层，使系统支持：

- **当前目标（B 模式）**：可切换单交易所，通过 `EXCHANGE` 环境变量选择 hyperliquid / okx / binance
- **未来扩展（C 模式）**：多交易所并行套利/对冲，factory 返回 `MultiExchangeAdapter` 即可，业务服务无感知

---

## 架构设计

### 新增目录结构

```
shared/exchange/
├── base.py                  # ExchangeAdapter 抽象基类
├── factory.py               # 工厂函数：根据 EXCHANGE 环境变量实例化
├── models.py                # 归一化数据模型
├── instrument_registry.py   # 合约规格缓存（动态拉取 + 静态兜底）
├── static_fallback.json     # 合约规格静态兜底数据
└── adapters/
    ├── hyperliquid.py       # 现有 shared/hl/client.py 内容迁移重构
    ├── okx.py
    └── binance.py
```

### 调用链变化

**改前（紧耦合）**：
```
market_data/app.py     →  shared/hl/client.py  →  hyperliquid-python-sdk
execution/app.py       →  shared/hl/client.py  →  hyperliquid-python-sdk
portfolio_state/app.py →  shared/hl/client.py  →  hyperliquid-python-sdk
```

**改后（解耦）**：
```
market_data/app.py     →  ExchangeAdapter（接口）
execution/app.py       →  ExchangeAdapter（接口）
portfolio_state/app.py →  ExchangeAdapter（接口）
                                ↓ factory.py 选择
              HyperliquidAdapter / OKXAdapter / BinanceAdapter
```

---

## 接口设计

### `shared/exchange/models.py` — 归一化数据模型

```python
@dataclass
class NormalizedTicker:
    symbol: str        # 统一符号，如 "BTC"
    mid_px: float
    bid: float
    ask: float
    spread_bps: float

@dataclass
class NormalizedPosition:
    symbol: str
    qty: float         # 正=多，负=空
    entry_px: float
    mark_px: float
    unreal_pnl: float
    side: str          # "long" | "short" | "flat"

@dataclass
class NormalizedAccountState:
    equity_usd: float
    cash_usd: float
    positions: dict[str, NormalizedPosition]
    open_orders: list[NormalizedOpenOrder]

@dataclass
class NormalizedOrderResult:
    client_order_id: str
    exchange_order_id: str
    status: str        # "ack" | "filled" | "rejected" | "canceled"
    filled_qty: float
    avg_px: float
    fee_usd: float
    latency_ms: int

@dataclass
class InstrumentSpec:
    symbol: str
    min_qty: float       # 最小下单量（以 base asset 计）
    qty_step: float      # 数量步长
    price_tick: float    # 价格最小变动
    contract_size: float # 合约面值（OKX 用）
    maker_fee: float
    taker_fee: float
```

### `shared/exchange/base.py` — 抽象接口

```python
class ExchangeAdapter(ABC):
    # ── 行情 ──────────────────────────────────
    @abstractmethod
    def get_all_mids(self, symbols: list[str]) -> dict[str, float]: ...

    @abstractmethod
    def get_l2_book(self, symbol: str, depth: int = 5) -> dict: ...

    @abstractmethod
    def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> float: ...

    @abstractmethod
    def get_open_interest(self, symbol: str) -> float: ...

    # ── 下单 ──────────────────────────────────
    @abstractmethod
    def place_limit(self, symbol: str, is_buy: bool, qty: float,
                    limit_px: float, tif: str = "GTC",
                    reduce_only: bool = False,
                    client_order_id: str | None = None) -> NormalizedOrderResult: ...

    @abstractmethod
    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool: ...

    @abstractmethod
    def get_order_status(self, symbol: str,
                         exchange_order_id: str) -> NormalizedOrderResult: ...

    # ── 账户 ──────────────────────────────────
    @abstractmethod
    def get_account_state(self) -> NormalizedAccountState: ...

    # ── 合约规格 ───────────────────────────────
    @abstractmethod
    def get_instrument_spec(self, symbol: str) -> InstrumentSpec: ...
```

### `shared/exchange/factory.py` — 工厂函数

```python
def create_exchange_adapter() -> ExchangeAdapter:
    exchange = os.environ["EXCHANGE"]  # "hyperliquid" | "okx" | "binance"
    if exchange == "hyperliquid":
        return HyperliquidAdapter(...)
    elif exchange == "okx":
        return OKXAdapter(...)
    elif exchange == "binance":
        return BinanceAdapter(...)
    else:
        raise ValueError(f"Unsupported exchange: {exchange}")
```

---

## 各交易所 Adapter 实现

### `adapters/hyperliquid.py`

- 基于现有 `hyperliquid-python-sdk`，从 `shared/hl/client.py` 迁移重构
- 凭证：`HL_PRIVATE_KEY` + `HL_ACCOUNT_ADDRESS`（`.env`）
- 符号映射：直接使用短名（"BTC"、"ETH"），无需转换

### `adapters/okx.py`

- SDK：`python-okx`（官方）
- 凭证：`OKX_API_KEY` + `OKX_API_SECRET` + `OKX_PASSPHRASE`
- 产品类型：SWAP（永续合约）
- 符号映射：`{"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP", ...}`
- **关键差异**：
  - 下单数量以"张"为单位，需用 `contract_size` 换算
  - `place_limit` 需推导 `posSide`（"long"/"short"）

### `adapters/binance.py`

- SDK：`binance-futures-connector`
- 凭证：`BINANCE_API_KEY` + `BINANCE_API_SECRET`
- 端点：FAPI（`https://fapi.binance.com`）
- 符号映射：`{"BTC": "BTCUSDT", "ETH": "ETHUSDT", ...}`
- **关键差异**：
  - 需开启双向持仓模式（hedgeMode）才能同时持有 LONG/SHORT
  - `place_limit` 需传 `positionSide`（LONG/SHORT/BOTH）

### `instrument_registry.py` — 合约规格缓存

```
查找顺序：Redis 缓存（TTL 3600s）→ 交易所 API 动态拉取 → static_fallback.json 兜底
```

---

## 服务层改动

改动模式统一，三个服务只替换最底层调用，业务逻辑（TWAP、幂等、双层决策、风控）完全不变：

```python
# 改前
from shared.hl.client import HyperliquidClient
hl = HyperliquidClient(...)

# 改后
from shared.exchange.factory import create_exchange_adapter
exchange = create_exchange_adapter()  # 启动时调用一次
```

| 服务 | 替换内容 | 影响调用数 |
|------|---------|----------|
| `market_data` | `get_all_mids / get_l2_book / get_recent_trades / get_funding_rate / get_open_interest` | ~30 处 |
| `execution` | `place_limit / cancel_order / get_order_status` | ~20 处 |
| `portfolio_state` | `get_account_state` | ~10 处 |

---

## 配置变更

### `.env` 新增项

```bash
# 交易所选择
EXCHANGE=hyperliquid   # hyperliquid | okx | binance

# OKX 凭证
OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=
OKX_HTTP_URL=https://www.okx.com

# Binance 凭证
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_FAPI_URL=https://fapi.binance.com
```

### `trading_params.json` 新增 `exchange_overrides`

```json
{
  "exchange_overrides": {
    "okx":     {"MIN_NOTIONAL_USD": 5.0, "SLICES": 3},
    "binance": {"MIN_NOTIONAL_USD": 5.0, "SLICES": 3}
  }
}
```

`config_loader.py` 合并逻辑：基础 version 参数 + `exchange_overrides[EXCHANGE]` 覆盖差异项。

### `static_fallback.json` 静态兜底

```json
{
  "okx": {
    "BTC": {"min_qty": 0.01, "qty_step": 0.01, "price_tick": 0.1, "contract_size": 0.01, "taker_fee": 0.0005},
    "ETH": {"min_qty": 0.1,  "qty_step": 0.1,  "price_tick": 0.01, "contract_size": 0.1,  "taker_fee": 0.0005}
  },
  "binance": {
    "BTC": {"min_qty": 0.001, "qty_step": 0.001, "price_tick": 0.1,  "contract_size": 1.0, "taker_fee": 0.0004},
    "ETH": {"min_qty": 0.001, "qty_step": 0.001, "price_tick": 0.01, "contract_size": 1.0, "taker_fee": 0.0004}
  }
}
```

---

## 错误处理

统一异常类型（adapter 内部转换，服务层无需感知）：

```python
class ExchangeError(Exception): ...
class OrderRejectedError(ExchangeError): ...
class RateLimitError(ExchangeError): ...
class InstrumentNotFoundError(ExchangeError): ...
```

各交易所限速（adapter 内各自维护独立令牌桶）：

| 交易所 | 下单限速 | 行情限速 |
|-------|---------|---------|
| OKX | 60 req/2s | 20 req/2s |
| Binance FAPI | 1200 weight/min | 2400 weight/min |

**DRY_RUN 模式**：adapter 层识别 `DRY_RUN=true`，`place_limit` 直接返回模拟结果，不发真实请求。

---

## 测试策略

```
tests/exchange/
├── test_hyperliquid_adapter.py   # 迁移现有 HL 测试
├── test_okx_adapter.py           # mock python-okx SDK
├── test_binance_adapter.py       # mock binance SDK
├── test_instrument_registry.py   # 缓存命中/缺失/兜底三路径
└── test_factory.py               # EXCHANGE 环境变量路由
```

所有 adapter 单元测试全部 mock SDK，不依赖真实网络。集成验证通过 `DRY_RUN=true` + 测试网完成。

---

## 上线路径

```
阶段 1 — 抽象层建立（不影响现有功能）
  ├── 建立 shared/exchange/ 目录和接口定义
  ├── 将现有 HL 代码迁移为 HyperliquidAdapter
  ├── 三个服务替换为 adapter 调用
  └── 验证：EXCHANGE=hyperliquid，现有全部测试通过

阶段 2 — OKX 接入
  ├── 实现 OKXAdapter + InstrumentRegistry
  ├── DRY_RUN=true 验证行情、下单、对账
  └── OKX 模拟盘端到端冒烟测试

阶段 3 — Binance 接入
  ├── 实现 BinanceAdapter
  ├── DRY_RUN=true 验证
  └── Binance testnet 端到端冒烟测试
```

阶段 1 对生产环境无感知；阶段 2/3 各自独立，可按需推进。

---

## 未来 C 模式扩展点

`factory.py` 返回 `MultiExchangeAdapter`，持有多个子 adapter，路由逻辑集中在一处，业务服务无需任何改动：

```python
class MultiExchangeAdapter(ExchangeAdapter):
    def __init__(self, adapters: dict[str, ExchangeAdapter]):
        self.adapters = adapters  # {"hyperliquid": ..., "okx": ...}

    def place_limit(self, symbol, exchange, ...):
        return self.adapters[exchange].place_limit(symbol, ...)
```
