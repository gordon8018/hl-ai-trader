# KuCoin 交易所支持设计文档

**日期**: 2026-03-19
**状态**: 已确认
**目标**: 在现有 Hyperliquid/OKX/Binance 基础上，增加对 KuCoin 永续合约的支持

---

## 背景与目标

项目已实现 Thin Adapter 模式的多交易所架构，支持 Hyperliquid、OKX、Binance 三家交易所。本方案延续此模式，新增 KuCoin 交易所支持。

**产品类型**: USDT 本位永续合约
**持仓模式**: 单向持仓（One-way）
**SDK 选择**: kucoin-futures-python-sdk（官方）

---

## 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 产品类型 | USDT 本位永续合约 | 与 OKX/Binance 一致 |
| SDK | kucoin-futures-python-sdk | 官方维护，与现有架构一致 |
| 持仓模式 | 单向持仓 | 与现有实现一致 |
| 交易对 | BTC/ETH/SOL/ADA/DOGE | 与 UNIVERSE 配置一致 |

---

## 文件变更

### 新增文件

| 文件 | 职责 |
|------|------|
| `shared/exchange/adapters/kucoin.py` | KucoinAdapter 实现（约 400-500 行） |
| `tests/exchange/test_kucoin_adapter.py` | 单元测试（约 200 行） |

### 修改文件

| 文件 | 变更 |
|------|------|
| `shared/exchange/factory.py` | 添加 `kucoin` 分支 |
| `shared/exchange/static_fallback.json` | 添加 KuCoin 合约规格 |
| `infra/.env.example` | 添加 KuCoin 凭证占位符 |
| `pyproject.toml` | 添加 `kucoin-futures-python-sdk` 依赖 |

---

## 符号映射

KuCoin 永续合约命名规则：
- 后缀为 `USDTM`（USDT-Margined）
- BTC 使用 `XBT` 而非 `BTC`

```python
SYMBOL_MAP = {
    "BTC": "XBTUSDTM",   # KuCoin 用 XBT 而非 BTC
    "ETH": "ETHUSDTM",
    "SOL": "SOLUSDTM",
    "ADA": "ADAUSDTM",
    "DOGE": "DOGEUSDTM",
}
```

---

## 凭证配置

```bash
# .env
EXCHANGE=kucoin   # hyperliquid | okx | binance | kucoin

# KuCoin 凭证
KUCOIN_API_KEY=
KUCOIN_API_SECRET=
KUCOIN_PASSPHRASE=
KUCOIN_FUTURES_URL=https://api-futures.kucoin.com
```

---

## API 调用映射

| 方法 | KuCoin API 端点 | 备注 |
|------|-----------------|------|
| `get_all_mids` | `/api/v1/ticker` | 获取所有合约行情 |
| `get_l2_book` | `/api/v1/level2/depth20` | 20 档深度 |
| `get_recent_trades` | `/api/v1/trade/history` | 最近成交 |
| `get_funding_rate` | `/api/v1/contracts/active` | 从合约信息获取 |
| `get_open_interest` | `/api/v1/contracts/active` | 从合约信息获取 |
| `place_limit` | `/api/v1/orders` | 下单 |
| `cancel_order` | `/api/v1/orders/{orderId}` | 撤单 |
| `get_order_status` | `/api/v1/orders/{orderId}` | 查询订单 |
| `get_account_state` | `/api/v1/account-overview` + `/api/v1/positions` | 账户 + 持仓 |
| `get_instrument_spec` | `/api/v1/contracts/active` | 合约规格 |

---

## SDK 初始化

```python
from kucoin_futures.client import Client

client = Client(
    key=api_key,
    secret=api_secret,
    passphrase=passphrase,
    url=base_url,
)
```

---

## DRY_RUN 模式

与现有 adapter 一致，检查 `os.environ["DRY_RUN"]`，返回模拟结果：

```python
@staticmethod
def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() == "true"

def place_limit(self, symbol, is_buy, qty, limit_px, ...):
    if self._is_dry_run():
        return NormalizedOrderResult(
            client_order_id=client_order_id,
            exchange_order_id=f"dry_kc_{int(time.time()*1000)}",
            status="ack",
            filled_qty=0.0,
            avg_px=0.0,
            fee_usd=0.0,
            latency_ms=...,
        )
    # 实际 SDK 调用
```

---

## 测试策略

与 OKX/Binance adapter 测试模式一致：

```python
# tests/exchange/test_kucoin_adapter.py
- test_get_all_mids_returns_float_dict
- test_get_l2_book_returns_normalized
- test_place_limit_ack
- test_place_limit_dry_run
- test_cancel_order_success
- test_get_order_status
- test_get_account_state_returns_normalized
- test_get_instrument_spec
- test_get_funding_rate
- test_get_open_interest
```

所有测试 mock SDK，不依赖真实网络。

---

## 实现任务列表

| 任务 | 描述 | 预计代码量 |
|------|------|-----------|
| Task 1 | 安装 kucoin-futures-python-sdk | pyproject.toml |
| Task 2 | 实现 KucoinAdapter（10 个抽象方法） | ~450 行 |
| Task 3 | 更新 factory.py 添加 kucoin 分支 | ~10 行 |
| Task 4 | 更新 static_fallback.json 添加 KuCoin 规格 | ~20 行 |
| Task 5 | 更新 .env.example 添加 KuCoin 凭证 | ~10 行 |
| Task 6 | 编写单元测试 | ~200 行 |
| Task 7 | DRY_RUN 冒烟测试 + 回归测试 | 验证 |

---

## 参考实现

- `shared/exchange/adapters/okx.py` — OKXAdapter 实现
- `shared/exchange/adapters/binance.py` — BinanceAdapter 实现
- `tests/exchange/test_okx_adapter.py` — OKX 测试模式
- `tests/exchange/test_binance_adapter.py` — Binance 测试模式