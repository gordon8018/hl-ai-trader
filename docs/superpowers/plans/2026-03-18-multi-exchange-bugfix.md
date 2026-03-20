# Multi-Exchange Bug Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复多交易所实现中的 Critical 和 Important 问题，使 OKX/Binance 在实盘环境下可正常运行，并恢复被回归的 19 个测试。

**Architecture:** 修复分三层：(1) 服务层——消除对 HL 专有方法的直接依赖，将 adapter 初始化移进 `main()`；(2) Execution 服务——清理 HL 死代码，用 adapter 标准接口替代；(3) Adapter 层——修正 Binance/OKX 的 N+1 调用和 positionSide 语义。

**Tech Stack:** Python 3.11, pytest, shared/exchange adapter abstraction

---

## Chunk 1: C3 — 修复模块级 adapter 初始化（恢复测试）

### Task 1: market_data — 将 `_exchange` 移进 `main()`

**Files:**
- Modify: `services/market_data/app.py:21-25`

- [ ] **Step 1: 确认当前失败的测试**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
python -m pytest tests/test_market_data_utils.py tests/test_market_data_1h.py -x --tb=short 2>&1 | head -30
```

Expected: 部分测试因 `KeyError: 'HL_PRIVATE_KEY'` 失败。

- [ ] **Step 2: 修改 `market_data/app.py`**

删除第 21-25 行的模块级 `_exchange` 赋值：
```python
# 删除这一行（约第 24 行）：
_exchange = create_exchange_adapter()
```

在 `main()` 函数体（约第 425 行）`bus = RedisStreams(REDIS_URL)` 之后添加：
```python
    _exchange = create_exchange_adapter()
```

注意：`_exchange` 在 `main()` 内部的嵌套函数（`fetch_recent_trades` 等）通过闭包使用，移进 `main()` 后正常工作。

- [ ] **Step 3: 验证测试恢复**

```bash
python -m pytest tests/test_market_data_utils.py tests/test_market_data_1h.py -x --tb=short 2>&1 | tail -10
```

Expected: 所有测试通过。

---

### Task 2: portfolio_state — 将 `_exchange` 移进 `main()`

**Files:**
- Modify: `services/portfolio_state/app.py:28-29`

- [ ] **Step 1: 确认当前失败的测试**

```bash
python -m pytest tests/test_portfolio_state_utils.py -x --tb=short 2>&1 | head -20
```

- [ ] **Step 2: 修改 `portfolio_state/app.py`**

删除第 29 行的模块级 `_exchange` 赋值：
```python
# 删除这一行（约第 29 行）：
_exchange = create_exchange_adapter()
```

在 `main()` 函数体（约第 165 行）`bus = RedisStreams(REDIS_URL)` 之后添加：
```python
    _exchange = create_exchange_adapter()
```

`_exchange` 在 `main()` 内部通过闭包使用（`get_account_state`、`get_all_mids`、`get_order_status` 调用位置都在 `main()` 内），无需 `nonlocal`。

- [ ] **Step 3: 验证测试恢复**

```bash
python -m pytest tests/test_portfolio_state_utils.py -x --tb=short 2>&1 | tail -10
```

- [ ] **Step 4: 运行全量测试确认无新回归**

```bash
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -15
```

Expected: 全部通过（原有测试数 207+）。

- [ ] **Step 5: 提交**

```bash
git add services/market_data/app.py services/portfolio_state/app.py
git commit -m "fix(services): move create_exchange_adapter() inside main() to fix test regressions"
```

---

## Chunk 2: C1 + C4 — 消除 market_data 对 HL 专有方法的依赖

### Task 3: 修改 `parse_l2_metrics` 以接受标准格式

**Files:**
- Modify: `services/market_data/app.py:172-246`

**背景：** 当前 `parse_l2_metrics` 期望 HL 格式 `{"levels": [[{px, sz}, ...], ...]}（dict 条目）`。
标准 `get_l2_book()` 返回 `{"bids": [[px_str, sz_str], ...], "asks": [...]}（list 条目）`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_market_data_utils.py` 中添加：

```python
def test_parse_l2_metrics_standard_format():
    """parse_l2_metrics should handle standard [[px, sz], ...] format."""
    from services.market_data.app import parse_l2_metrics
    book = {
        "bids": [["50000.0", "1.5"], ["49990.0", "2.0"], ["49980.0", "3.0"],
                 ["49970.0", "1.0"], ["49960.0", "0.5"]],
        "asks": [["50010.0", "1.0"], ["50020.0", "2.5"], ["50030.0", "1.5"],
                 ["50040.0", "0.8"], ["50050.0", "1.2"]],
    }
    metrics = parse_l2_metrics(book)
    assert "spread_bps" in metrics
    assert metrics["spread_bps"] > 0
    assert "book_imbalance_l1" in metrics
    assert "microprice" in metrics
    assert abs(metrics["microprice"] - 50000.0) < 100

def test_parse_l2_metrics_empty_book():
    from services.market_data.app import parse_l2_metrics
    assert parse_l2_metrics({}) == {}
    assert parse_l2_metrics({"bids": [], "asks": []}) == {}
```

```bash
python -m pytest tests/test_market_data_utils.py::test_parse_l2_metrics_standard_format -xvs 2>&1 | tail -15
```

Expected: FAIL（当前代码解析 `levels` 键，新格式无法工作）。

- [ ] **Step 2: 修改 `parse_l2_metrics`**

将函数签名改为接受标准格式 `{"bids": [[px, sz], ...], "asks": [[px, sz], ...]}`，同时保持内部逻辑完全不变，只修改数据提取部分：

```python
def parse_l2_metrics(resp_json: dict) -> dict:
    if not isinstance(resp_json, dict):
        return {}

    # Support standard format: {"bids": [[px, sz], ...], "asks": [[px, sz], ...]}
    raw_bids = resp_json.get("bids")
    raw_asks = resp_json.get("asks")
    if not isinstance(raw_bids, list) or not isinstance(raw_asks, list):
        return {}
    if not raw_bids or not raw_asks:
        return {}

    def _entry_to_dict(entry) -> dict:
        """Convert [px, sz] list to {"px": px, "sz": sz} dict."""
        if isinstance(entry, dict):
            return entry
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            return {"px": entry[0], "sz": entry[1]}
        return {}

    bids = [_entry_to_dict(e) for e in raw_bids]
    asks = [_entry_to_dict(e) for e in raw_asks]
    # ... 以下保持原有逻辑不变（bid_px, ask_px 等计算）
```

具体：将原来的：
```python
    levels = resp_json.get("levels")
    if not isinstance(levels, list) or len(levels) < 2:
        return {}
    bids = levels[0] if isinstance(levels[0], list) else []
    asks = levels[1] if isinstance(levels[1], list) else []
```
全部替换为新的提取逻辑（如上），其余 200 行计算逻辑（bid_px/ask_px/spread_bps 等）**保持原样不动**。

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/test_market_data_utils.py::test_parse_l2_metrics_standard_format tests/test_market_data_utils.py::test_parse_l2_metrics_empty_book -xvs 2>&1 | tail -10
```

Expected: PASS。

---

### Task 4: 替换 `get_l2_book_raw()` 为标准 `get_l2_book()`

**Files:**
- Modify: `services/market_data/app.py:515-520`（L2 poll 循环）

- [ ] **Step 1: 修改 L2 轮询代码**

找到约第 515 行：
```python
                    l2_raw = _exchange.get_l2_book_raw(sym, n_sig_figs=n_sig_figs, mantissa=mantissa)
                    metrics = parse_l2_metrics(l2_raw)
```

替换为：
```python
                    l2_book = _exchange.get_l2_book(sym, depth=10)
                    metrics = parse_l2_metrics(l2_book)
```

同时删除第 31-34 行的 `L2_N_SIGFIGS` 和 `L2_MANTISSA` 环境变量（这两个是 HL 精度参数，标准接口不支持），以及第 33 行变量：
```python
# 删除这两行：
L2_N_SIGFIGS = os.environ.get("MD_L2_N_SIGFIGS", "").strip()
L2_MANTISSA = os.environ.get("MD_L2_MANTISSA", "").strip()
```

以及 L2 poll 循环中的参数解析：
```python
# 删除这两行（约第 513-514 行）：
                n_sig_figs = int(L2_N_SIGFIGS) if L2_N_SIGFIGS else None
                mantissa = int(L2_MANTISSA) if L2_MANTISSA else None
```

- [ ] **Step 2: 运行全量测试**

```bash
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -10
```

---

### Task 5: 替换 `get_meta_and_asset_ctxs_raw()` 为标准方法

**Files:**
- Modify: `services/market_data/app.py:490-500`（perp ctx poll 块）

**背景：** `perp_ctx_cache` 在特征计算中被用来取 `funding`、`openInterest`、`premium`、`markPx`、`oraclePx`。
标准接口 `get_funding_rate(sym)` 和 `get_open_interest(sym)` 覆盖前两个字段；
`premium`/`markPx`/`oraclePx` 是 HL 专有字段，对 OKX/Binance 设为 0（不影响功能）。

- [ ] **Step 1: 修改 perp ctx poll 块**

找到约第 492-497 行：
```python
                try:
                    # Use adapter's raw method for batch processing
                    ctx_raw = _exchange.get_meta_and_asset_ctxs_raw()
                    perp_ctx_cache = parse_meta_and_asset_ctxs(ctx_raw)
                    last_perp_ctx_ts = now
```

替换为：
```python
                try:
                    for sym in UNIVERSE:
                        perp_ctx_cache[sym] = {
                            "funding": _exchange.get_funding_rate(sym),
                            "openInterest": _exchange.get_open_interest(sym),
                        }
                    last_perp_ctx_ts = now
```

- [ ] **Step 2: 删除不再使用的函数**

检查 `parse_meta_and_asset_ctxs` 函数（约第 149-165 行）是否还有其他调用：

```bash
grep -n "parse_meta_and_asset_ctxs" services/market_data/app.py
```

如果只被刚刚删掉的那一处使用，则删除该函数定义（约 16 行）。

- [ ] **Step 3: 运行全量测试**

```bash
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -10
```

- [ ] **Step 4: 提交**

```bash
git add services/market_data/app.py
git commit -m "fix(market_data): replace HL-specific raw methods with standard adapter interface"
```

---

## Chunk 3: C2 + I6 — Execution 服务清理

### Task 6: 替换 `get_sz_decimals` 为 adapter 标准接口

**Files:**
- Modify: `services/execution/app.py`

**背景：** `get_sz_decimals(http, sym)` 直接调用 HL HTTP 端点获取 `szDecimals`。
替代方案：用 `_exchange.get_instrument_spec(sym).qty_step` 推导：
`sz_decimals = max(0, round(-log10(qty_step)))` if `qty_step < 1` else `0`。

`_exchange` 在 DRY_RUN 模式下也需要创建（adapter 自身不调用真实 API），
以便 `get_instrument_spec` 可以返回 static fallback 数据。

- [ ] **Step 1: 写失败测试**

在 `tests/test_execution_utils.py` 中添加（若文件不存在则在已有测试文件中添加）：

```python
def test_qty_decimals_from_spec():
    """_qty_decimals should convert qty_step to decimal places."""
    from services.execution.app import _qty_decimals_from_step
    assert _qty_decimals_from_step(0.001) == 3
    assert _qty_decimals_from_step(0.01) == 2
    assert _qty_decimals_from_step(1.0) == 0
    assert _qty_decimals_from_step(0.1) == 1
    assert _qty_decimals_from_step(0.0001) == 4
```

```bash
python -m pytest tests/ -k "test_qty_decimals" -xvs 2>&1 | tail -10
```

Expected: FAIL（函数不存在）。

- [ ] **Step 2: 在 `execution/app.py` 中添加辅助函数，替换死代码**

**2a. 删除模块级死代码（约第 6、49-51、81、97 行）：**
```python
# 删除：
import httpx
HL_HTTP_URL = os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz")
HL_ACCOUNT_ADDRESS = os.environ.get("HL_ACCOUNT_ADDRESS", "").strip()
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "").strip()
META_TTL_S = int(os.environ.get("META_TTL_S", "3600"))
_META_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}}
```

**2b. 删除死函数（约第 162-190 行）：**
```python
# 删除整个 _fetch_meta 和 get_sz_decimals 函数定义
```

**2c. 添加新辅助函数（放在 `round_size` 定义附近）：**
```python
import math

def _qty_decimals_from_step(qty_step: float) -> int:
    """Convert qty_step to number of decimal places.

    Examples: 0.001 -> 3, 0.01 -> 2, 1.0 -> 0
    """
    if qty_step <= 0 or qty_step >= 1.0:
        return 0
    return max(0, round(-math.log10(qty_step)))
```

- [ ] **Step 3: 修改 `main()` 中 `_exchange` 初始化（约第 628-636 行）**

将：
```python
    # HTTP client for metadata fetching
    http = httpx.Client(timeout=6.0)

    # Initialize exchange adapter for real trading
    _exchange = None
    if not DRY_RUN:
        if not (HL_ACCOUNT_ADDRESS and HL_PRIVATE_KEY):
            raise RuntimeError(
                "DRY_RUN=false but HL_ACCOUNT_ADDRESS / HL_PRIVATE_KEY not set."
            )
        _exchange = create_exchange_adapter()
```

替换为：
```python
    # Initialize exchange adapter (always, even in DRY_RUN for instrument spec lookup)
    _exchange = create_exchange_adapter()
```

- [ ] **Step 4: 替换 `get_sz_decimals` 调用（约第 935、966 行）**

将：
```python
                        sz_decimals = get_sz_decimals(http, s.symbol)
                        limit_px = format_price(limit_px, sz_decimals)
                        ...
                        qty = round_size(s.qty, sz_decimals)
```

替换为：
```python
                        try:
                            spec = _exchange.get_instrument_spec(s.symbol)
                            sz_decimals = _qty_decimals_from_step(spec.qty_step)
                        except Exception:
                            sz_decimals = 0
                        limit_px = format_price(limit_px, sz_decimals)
                        ...
                        qty = round_size(s.qty, sz_decimals)
```

- [ ] **Step 5: 运行测试**

```bash
python -m pytest tests/ -k "test_qty_decimals" -xvs 2>&1 | tail -10
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -10
```

- [ ] **Step 6: 提交**

```bash
git add services/execution/app.py
git commit -m "fix(execution): replace HL-specific sz_decimals with adapter.get_instrument_spec"
```

---

## Chunk 4: I1 — 修正 Binance/OKX 对冲模式 positionSide

### Task 7: Binance — 改为单向模式 `positionSide=BOTH`

**Files:**
- Modify: `shared/exchange/adapters/binance.py:247,261`

**背景：** 当前对冲模式 `LONG/SHORT` 映射会导致平仓订单路由错误。
改为单向模式（`BOTH`），与 Hyperliquid 行为对齐，`reduce_only` 用于控制方向。

- [ ] **Step 1: 写失败测试**

在 `tests/exchange/test_binance_adapter.py` 中检查现有下单测试，添加：

```python
def test_place_limit_uses_oneway_position_side(monkeypatch):
    """place_limit should use positionSide=BOTH (one-way mode), not LONG/SHORT."""
    monkeypatch.setenv("DRY_RUN", "false")
    adapter = BinanceAdapter.__new__(BinanceAdapter)
    adapter._api_key = "k"
    adapter._api_secret = "s"
    adapter._base_url = "https://fapi.binance.com"
    adapter._instrument_cache = {}
    adapter._client = MagicMock()
    adapter._client.new_order.return_value = {
        "orderId": 12345,
        "status": "NEW",
        "executedQty": "0",
        "avgPrice": "0",
    }
    # Mock get_instrument_spec
    from shared.exchange.models import InstrumentSpec
    adapter._instrument_cache["BTC"] = InstrumentSpec(
        symbol="BTC", min_qty=0.001, qty_step=0.001,
        price_tick=0.1, contract_size=1.0, maker_fee=0.0002, taker_fee=0.0004
    )
    adapter.place_limit("BTC", is_buy=True, qty=0.01, limit_px=50000.0)
    call_kwargs = adapter._client.new_order.call_args[1]
    assert call_kwargs.get("positionSide") == "BOTH"
```

```bash
DRY_RUN=false python -m pytest tests/exchange/test_binance_adapter.py::test_place_limit_uses_oneway_position_side -xvs 2>&1 | tail -15
```

Expected: FAIL（当前返回 "LONG"）。

- [ ] **Step 2: 修改 `binance.py` 的 `place_limit`**

找到约第 246-248 行的对冲模式注释和 `position_side` 赋值，替换为：

```python
        # One-way mode: positionSide=BOTH matches Hyperliquid's single-direction behavior.
        # reduce_only controls whether this closes an existing position.
        position_side = "BOTH"
        side = "BUY" if is_buy else "SELL"
```

- [ ] **Step 3: 运行测试**

```bash
DRY_RUN=false python -m pytest tests/exchange/test_binance_adapter.py -xvs 2>&1 | tail -15
```

---

### Task 8: OKX — 改为单向模式 `posSide=net`

**Files:**
- Modify: `shared/exchange/adapters/okx.py:269`

- [ ] **Step 1: 写失败测试**

在 `tests/exchange/test_okx_adapter.py` 中添加：

```python
def test_place_limit_uses_oneway_pos_side(monkeypatch):
    """place_limit should use posSide=net (one-way mode)."""
    monkeypatch.setenv("DRY_RUN", "false")
    adapter = OKXAdapter.__new__(OKXAdapter)
    adapter._api_key = "k"
    adapter._api_secret = "s"
    adapter._passphrase = "p"
    adapter._base_url = "https://www.okx.com"
    adapter._instrument_cache = {}
    adapter._trade_api = MagicMock()
    adapter._trade_api.place_order.return_value = {
        "code": "0",
        "data": [{"ordId": "123456", "sCode": "0", "sMsg": ""}],
    }
    from shared.exchange.models import InstrumentSpec
    adapter._instrument_cache["BTC"] = InstrumentSpec(
        symbol="BTC", min_qty=0.001, qty_step=0.001,
        price_tick=0.1, contract_size=0.01, maker_fee=0.0002, taker_fee=0.0004
    )
    adapter.place_limit("BTC", is_buy=True, qty=0.01, limit_px=50000.0)
    call_kwargs = adapter._trade_api.place_order.call_args[1]
    assert call_kwargs.get("posSide") == "net"
```

```bash
DRY_RUN=false python -m pytest tests/exchange/test_okx_adapter.py::test_place_limit_uses_oneway_pos_side -xvs 2>&1 | tail -15
```

Expected: FAIL（当前为 "long"/"short"）。

- [ ] **Step 2: 修改 `okx.py` 的 `place_limit`**

找到约第 269 行：
```python
        pos_side = "long" if is_buy else "short"
```

替换为：
```python
        # One-way mode: posSide=net matches Hyperliquid's single-direction behavior.
        pos_side = "net"
```

- [ ] **Step 3: 运行测试**

```bash
DRY_RUN=false python -m pytest tests/exchange/test_okx_adapter.py tests/exchange/test_binance_adapter.py -v 2>&1 | tail -20
```

- [ ] **Step 4: 提交**

```bash
git add shared/exchange/adapters/binance.py shared/exchange/adapters/okx.py
git commit -m "fix(adapters): switch Binance/OKX to one-way mode (positionSide=BOTH / posSide=net)"
```

---

## Chunk 5: I2 — `get_order_status` 传空 symbol

### Task 9: 在 OID metadata 中存入 `symbol`，轮询时使用

**Files:**
- Modify: `services/portfolio_state/app.py:307-320`（注册 OID 处）
- Modify: `services/portfolio_state/app.py:531`（`get_order_status` 调用处）

- [ ] **Step 1: 修改 OID 注册（存入 symbol）**

找到约第 305-320 行的 `if rep.exchange_order_id:` 块，将 meta 字典改为：
```python
                            meta = {
                                "added_ts": now,
                                "last_poll_ts": 0.0,
                                "error_count": 0,
                                "backoff_ms": ORDER_STATUS_ERROR_BASE_BACKOFF_MS,
                                "symbol": rep.symbol,  # <-- 新增
                            }
```

- [ ] **Step 2: 修改 `get_order_status` 调用**

找到约第 531 行：
```python
                            order_result = _exchange.get_order_status("", oid_s)
```

替换为：
```python
                            order_result = _exchange.get_order_status(
                                meta.get("symbol", ""), oid_s
                            )
```

注意：确认 `meta` 变量在该调用位置可访问（它来自 `_select_orders_to_poll` 返回的 `(oid_s, meta)` 元组）。

- [ ] **Step 3: 运行全量测试**

```bash
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -10
```

- [ ] **Step 4: 提交**

```bash
git add services/portfolio_state/app.py
git commit -m "fix(portfolio_state): store symbol in OID metadata and pass to get_order_status"
```

---

## Chunk 6: I3 + I4 — 消除 N+1 API 调用

### Task 10: Binance — `get_all_mids` 改为单次 `book_ticker()`

**Files:**
- Modify: `shared/exchange/adapters/binance.py:115-135`

- [ ] **Step 1: 写失败测试**（验证只发一次 API 调用）

在 `tests/exchange/test_binance_adapter.py` 中添加：
```python
def test_get_all_mids_single_api_call(monkeypatch):
    """get_all_mids should call book_ticker once (no per-symbol loop)."""
    monkeypatch.setenv("DRY_RUN", "false")
    adapter = BinanceAdapter.__new__(BinanceAdapter)
    adapter._api_key = "k"
    adapter._api_secret = "s"
    adapter._base_url = "https://fapi.binance.com"
    adapter._instrument_cache = {}
    adapter._client = MagicMock()
    adapter._client.book_ticker.return_value = [
        {"symbol": "BTCUSDT", "bidPrice": "49990.0", "askPrice": "50010.0"},
        {"symbol": "ETHUSDT", "bidPrice": "2999.0", "askPrice": "3001.0"},
    ]
    result = adapter.get_all_mids(["BTC", "ETH"])
    # Should call book_ticker exactly once (no symbol argument = all symbols)
    assert adapter._client.book_ticker.call_count == 1
    assert adapter._client.book_ticker.call_args == call()  # no args
    assert abs(result["BTC"] - 50000.0) < 1
    assert abs(result["ETH"] - 3000.0) < 1
```

```bash
DRY_RUN=false python -m pytest tests/exchange/test_binance_adapter.py::test_get_all_mids_single_api_call -xvs 2>&1 | tail -15
```

Expected: FAIL（当前有 per-symbol 循环）。

- [ ] **Step 2: 修改 `BinanceAdapter.get_all_mids`**

将现有实现（约第 116-134 行）替换为：
```python
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
```

- [ ] **Step 3: 运行测试**

```bash
DRY_RUN=false python -m pytest tests/exchange/test_binance_adapter.py -v 2>&1 | tail -20
```

---

### Task 11: OKX — `get_open_interest` 内联 ticker 请求

**Files:**
- Modify: `shared/exchange/adapters/okx.py:232-235`

- [ ] **Step 1: 写失败测试**

在 `tests/exchange/test_okx_adapter.py` 中添加：
```python
def test_get_open_interest_no_nested_get_all_mids(monkeypatch):
    """get_open_interest should NOT call get_all_mids (avoid nested HTTP calls)."""
    monkeypatch.setenv("DRY_RUN", "false")
    adapter = OKXAdapter.__new__(OKXAdapter)
    adapter._api_key = "k"
    adapter._api_secret = "s"
    adapter._passphrase = "p"
    adapter._base_url = "https://www.okx.com"
    adapter._instrument_cache = {}
    adapter._public_api = MagicMock()
    adapter._market_api = MagicMock()

    from shared.exchange.models import InstrumentSpec
    adapter._instrument_cache["BTC"] = InstrumentSpec(
        symbol="BTC", min_qty=0.001, qty_step=0.001,
        price_tick=0.1, contract_size=0.01, maker_fee=0.0002, taker_fee=0.0004
    )
    adapter._public_api.get_open_interest.return_value = {
        "code": "0", "data": [{"oi": "1000"}]
    }
    adapter._market_api.get_ticker.return_value = {
        "code": "0", "data": [{"bidPx": "49990", "askPx": "50010"}]
    }

    result = adapter.get_open_interest("BTC")
    # market_api.get_ticker called directly, NOT via get_all_mids
    adapter._market_api.get_ticker.assert_called_once()
    assert result > 0
```

```bash
DRY_RUN=false python -m pytest tests/exchange/test_okx_adapter.py::test_get_open_interest_no_nested_get_all_mids -xvs 2>&1 | tail -15
```

- [ ] **Step 2: 修改 `OKXAdapter.get_open_interest`**

将：
```python
                mid = self.get_all_mids([symbol]).get(symbol, 0)
```

替换为：
```python
                # Get mid price directly to avoid nested get_all_mids HTTP call
                mid = 0.0
                try:
                    ticker_resp = self._market_api.get_ticker(instId=inst_id)
                    if ticker_resp and "data" in ticker_resp and ticker_resp["data"]:
                        d = ticker_resp["data"][0]
                        bid = float(d.get("bidPx", 0) or 0)
                        ask = float(d.get("askPx", 0) or 0)
                        if bid > 0 and ask > 0:
                            mid = (bid + ask) / 2
                except Exception:
                    pass
```

- [ ] **Step 3: 运行测试**

```bash
DRY_RUN=false python -m pytest tests/exchange/test_okx_adapter.py tests/exchange/test_binance_adapter.py -v 2>&1 | tail -20
```

- [ ] **Step 4: 运行全量测试**

```bash
python -m pytest tests/ --tb=short -q 2>&1 | tail -15
```

Expected: 全部通过，exchange/ 测试也包含在内。

- [ ] **Step 5: 提交**

```bash
git add shared/exchange/adapters/binance.py shared/exchange/adapters/okx.py
git commit -m "fix(adapters): eliminate N+1 API calls in BinanceAdapter.get_all_mids and OKXAdapter.get_open_interest"
```

---

## 最终验证

- [ ] **运行完整测试套件**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: 所有测试通过（包含原有 207 + 新增测试）。

- [ ] **DRY_RUN 冒烟测试（可选）**

```bash
DRY_RUN=true EXCHANGE=binance python -c "
from shared.exchange.factory import create_exchange_adapter
e = create_exchange_adapter()
print(e.get_all_mids(['BTC', 'ETH']))
print(e.get_instrument_spec('BTC'))
"
```

Expected: 返回 mock 数据，无异常。
