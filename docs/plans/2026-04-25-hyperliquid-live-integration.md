# Hyperliquid Realtime + Live Trading Implementation Plan

> For Hermes: use test-driven-development skill and implement this plan in small verified steps.

Goal: 在 hl-ai-trader 项目中，基于现有 Hyperliquid 配置，补齐“实时行情接入 + 可验证的实盘交易入口”。

Architecture: 保持现有 thin adapter 架构不变。市场数据层优先在 HyperliquidAdapter 内增加 allMids WebSocket 缓存，并让 market_data 服务无感知复用；交易层保留 execution service 的 live path，再补一个基于现有环境变量的 Hyperliquid smoke/probe 脚本作为实盘接入入口与验证工具。

Tech Stack: Python, httpx, websockets, hyperliquid-python-sdk, pytest.

---

### Task 1: 为 HyperliquidAdapter 增加 WebSocket 缓存测试
Objective: 先锁定实时行情缓存的目标行为。

Files:
- Modify: `tests/exchange/test_hyperliquid_adapter.py`
- Modify: `shared/exchange/adapters/hyperliquid.py`

### Task 2: 实现 allMids WebSocket 缓存与 REST fallback
Objective: 让 get_all_mids 优先使用新鲜 WS 缓存，过期时自动退回 REST。

Files:
- Modify: `shared/exchange/adapters/hyperliquid.py`
- Test: `tests/exchange/test_hyperliquid_adapter.py`

### Task 3: 为实盘接入入口写测试
Objective: 先定义基于现有配置的 Hyperliquid probe 脚本行为。

Files:
- Create: `tests/scripts/test_hyperliquid_live_probe.py`
- Create: `scripts/hyperliquid_live_probe.py`

### Task 4: 实现 Hyperliquid probe 脚本
Objective: 提供只读探针 + 可选下单探针，默认不产生真实交易副作用。

Files:
- Create: `scripts/hyperliquid_live_probe.py`
- Test: `tests/scripts/test_hyperliquid_live_probe.py`

### Task 5: 更新运行说明并做回归
Objective: 写清楚如何用现有配置启动 Hyperliquid 实时行情与实盘模式，并验证核心测试通过。

Files:
- Modify: `README.md`
- Run: `pytest tests/exchange/test_hyperliquid_adapter.py tests/scripts/test_hyperliquid_live_probe.py -q`
- Run: `pytest tests/test_market_data_utils.py tests/test_execution_utils.py -q`
