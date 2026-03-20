# 双层决策架构变更日志

**日期：** 2026-03-16
**分支：** `feature/dual-layer-decision` → merged to `main`
**Commits：** 10 个（3ba6fbd → b2a495b）
**变更规模：** 14 个文件，+1507 行，-66 行

---

## 背景

原系统（v8）实盘分析发现结构性亏损：
- 日均交易 400+ 次，手续费/毛利润比率 142%
- 胜率 34.2%，而盈亏平衡需要 51.6%（差距 -17.4%）
- 资金利用率约 2%（MAX_GROSS 过低 + EWMA 平滑过强）

**解决方案：** 将 15 分钟单层决策改为 1H LLM 定方向 + 15m 规则确认双层架构。

---

## 架构变更

### 原架构
```
md.features.15m → ai_decision (LLM 每 15 分钟) → alpha.target
```

### 新架构
```
md.features.1h  → Layer 1: LLM 方向判断 → DirectionBias (Redis 缓存 70 分钟)
                         ↓
md.features.15m → Layer 2: 3 信号确认规则 → TargetPortfolio → alpha.target
```

---

## 文件变更详情

### 1. `shared/schemas.py` — 新增 3 个 Pydantic v2 模型

```python
class SymbolBias(BaseModel):
    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    confidence: float = Field(ge=0.0, le=1.0)

class DirectionBias(BaseModel):
    asof_minute: str
    valid_until_minute: str
    market_state: Literal["TRENDING", "SIDEWAYS", "VOLATILE", "EMERGENCY"]
    biases: List[SymbolBias]
    rationale: str = ""

class FeatureSnapshot1h(BaseModel):
    asof_minute: str
    window_start_minute: str
    universe: List[str]
    mid_px: Dict[str, float]
    # 1H 特征
    ret_1h, vol_1h, trend_1h, funding_rate, basis_bps,
    oi_change_1h, vol_regime, trend_agree, book_imbalance_l10
    # 新增 4H 特征
    ret_4h, vol_4h, trend_4h
    # 新增 1H 聚合
    rsi_14_1h, aggr_delta_1h
```

---

### 2. `services/market_data/app.py` — 1H 特征计算与发布

**新增函数：**

| 函数 | 说明 |
|------|------|
| `rsi_from_hist(hist, now_ts, period=14)` | 基于价格历史计算 RSI |
| `compute_1h_features(sym, hist, now_ts, aggr_delta_hist)` | 计算单个 symbol 的 1H 特征，返回 `ret_1h/4h`、`vol_1h/4h`、`trend_1h/4h`、`rsi_14_1h`、`aggr_delta_1h` |

**新增常量：**
```python
MD_PRICE_HISTORY_MINUTES = int(os.environ.get("MD_PRICE_HISTORY_MINUTES", "310"))
STREAM_OUT_1H = "md.features.1h"
```

**滚动窗口扩展：**
- `mids_hist` maxlen：固定 90 分钟 → `MD_PRICE_HISTORY_MINUTES * 30`（约 9300 点，覆盖 4H 回溯）
- 新增 `aggr_delta_hist`：每 symbol 70 点缓冲，用于 1H aggr_delta 汇总

**1H 发布逻辑（每小时整点触发）：**
```python
if _now_dt.minute == 0:   # 整点触发（注：不用 second==0，避免 2s 轮询漏触发）
    fs1h = FeatureSnapshot1h(...)
    bus.xadd_json(STREAM_OUT_1H, require_env({...}))
    MSG_OUT.labels(SERVICE, STREAM_OUT_1H).inc()
```

**Bug fix（代码审查发现）：**
- 去掉 `_now_dt.second == 0` 判断（2 秒轮询永远不会恰好 second=0，导致从不触发）
- `asof_minute` 改用 `last_emitted_minute`（与 1m/15m 快照惯例一致）
- `xadd_json` 加上 `require_env()` 包装

---

### 3. `services/ai_decision/app.py` — 双层决策核心

#### 新增常量
```python
STREAM_IN_1H = "md.features.1h"
DIRECTION_BIAS_KEY = "latest.direction_bias"   # Redis key
GROUP_1H = "ai_grp_1h"
MIN_NOTIONAL_USD = 50.0       # 小于 $50 的仓位过滤
MAX_TRADES_PER_DAY = 30       # 日交易上限

# mode-aware MAX_GROSS
MAX_GROSS_TRENDING_HIGH = 0.65   # 置信度 >= 0.70
MAX_GROSS_TRENDING_MID  = 0.45   # 置信度 < 0.70
MAX_GROSS_SIDEWAYS      = 0.00   # 横盘
MAX_GROSS_VOLATILE      = 0.20   # 高波动

MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD = 0.70
AI_SMOOTH_ALPHA_MID = 0.40        # 中置信度 EWMA alpha
AI_SMOOTH_ALPHA_HIGH = 0.60       # 高置信度（从 0.50 调整）
```

#### Layer 1 相关函数

| 函数 | 说明 |
|------|------|
| `SYSTEM_PROMPT_1H` | 方向偏差 Prompt：含 SIDEWAYS 过滤（STEP -1）、TRENDING/VOLATILE 判断（STEP 1）、3+ 信号要求（STEP 2）、置信度校准（STEP 3） |
| `parse_direction_bias(raw_text, universe)` | 解析 LLM JSON → DirectionBias，缺失 symbol 填充 FLAT，sanitize market_state |
| `is_direction_bias_valid(bias, asof_minute)` | 检查 DirectionBias 未过期 |
| `get_cached_direction_bias(bus)` | 从 Redis 读取缓存的 DirectionBias |
| `store_direction_bias(bus, bias)` | 写入 Redis，TTL 4200 秒（70 分钟） |
| `build_1h_user_payload(fs1h, universe)` | 构建发给 LLM 的 1H 特征 JSON |
| `process_1h_message(fs1h, bus, llm_cfg)` | Layer 1 主函数：调用 LLM → 解析 → 缓存 |

**Bug fix（代码审查发现）：**
- `import re` 移到模块级别
- `valid_until` 格式从 `%H:00:00Z`（截断分钟）改为 `%H:%M:00Z`

#### Layer 2 规则引擎

```python
def apply_direction_confirmation(fs, bias, universe, min_confirm=3):
    """3 信号确认规则，返回目标权重"""
    # market_state == SIDEWAYS → 全零
    # LONG 确认信号（6 个，需 3+）：
    #   funding_rate > 0, basis_bps > 0, oi_change_15m > 0,
    #   book_imbalance_l5 > 0.10, aggr_delta_5m > 0, trend_strength_15m > 0.6
    # SHORT 确认信号（对称，trend_strength > 0.6 为趋势存在信号，方向无关）
    # 权重 = CAP * confidence（BTC/ETH 用 CAP_BTC_ETH，其他用 CAP_ALT）
```

#### 资金利用率辅助函数

```python
def select_max_gross(market_state, confidence) -> float:
    """根据市场状态和置信度选择 MAX_GROSS"""

def select_smooth_alpha(confidence) -> float:
    """动态 EWMA alpha：0.25 / 0.40 / 0.60"""

def apply_min_notional(weights, equity_usd, min_notional_usd=50.0) -> dict:
    """过滤名义值低于阈值的仓位"""

def get_equity_usd(bus) -> float:
    """从 Redis state 读取权益，默认 1000.0"""
```

#### 日交易上限辅助函数

```python
DAILY_TRADE_CAP_KEY_PREFIX = "daily_trade_count:"
LAST_TRADE_TS_KEY = "last_trade_timestamp"

check_daily_trade_cap(bus)       # 是否已达上限
increment_daily_trade_count(bus) # 计数 +1，TTL 24h
get_minutes_since_last_trade(bus)
record_trade_executed(bus)
```

#### `to_major_cycle_id()` 改动

```python
# 原：15 分钟对齐
dt = dt.replace(minute=dt.minute // 15 * 15, ...)
# 新：1 小时对齐
dt = dt.replace(minute=0, second=0, microsecond=0)
```

#### 双循环 `main()` 结构

```python
while True:
    # ── Layer 1：非阻塞轮询 md.features.1h ──
    msgs_1h = bus.xreadgroup_json(STREAM_IN_1H, ..., block_ms=0)
    # → process_1h_message() → 更新 cached_bias

    # ── Layer 2：阻塞 5s 轮询 md.features.15m ──
    msgs_15m = bus.xreadgroup_json(STREAM_IN, ..., block_ms=5000)
    # → 刷新过期 cached_bias
    # → 日交易上限检查（达限则 HOLD）
    # → 频率门控（30 分钟内 + 置信度 < 0.70 → HOLD）
    # → apply_direction_confirmation() 或 fallback 基线
    # → select_max_gross / select_smooth_alpha / apply_min_notional
    # → 发布 TargetPortfolio
    # → REBALANCE 时：increment_daily_trade_count + record_trade_executed
```

---

### 4. `backtester/` — 离线回测模块

#### `backtester/replay.py`

从 Redis `md.features.15m` 读取历史，模拟双层逻辑，生成 `Prediction` 列表：

```python
run_replay(days=7, use_llm=False, outcome_horizon_min=60) -> List[Prediction]
```

核心类：
```python
class Prediction:
    asof_minute, symbol, direction, entry_px, outcome_px
    market_state, bias_confidence
    @property ret: float      # 持仓收益率
    @property won: bool       # 是否盈利
```

Layer 1 规则替代（`build_simple_bias_from_features`）：无 LLM 时用规则生成 DirectionBias。

#### `backtester/evaluator.py`

```python
evaluate(predictions, fee_per_trade_usd=0.006) -> dict
# 返回按 ALL / market_state / symbol 分组的指标：
# win_rate, avg_win_pct, avg_loss_pct, reward_risk,
# breakeven_win_rate, est_net_pnl_per_trade_usd

print_report(results) -> None   # 打印格式化报告
```

#### `backtester/run_backtest.sh`

```bash
./backtester/run_backtest.sh                        # 7 天干跑
./backtester/run_backtest.sh --days 14              # 14 天
./backtester/run_backtest.sh --use-llm              # 真实 LLM
./backtester/run_backtest.sh --outcome-horizon 30   # 30 分钟收益窗口
./backtester/run_backtest.sh --compare 3 14         # 对比两个窗口
```

---

### 5. `infra/.env.example` — 新增参数模板

新增以下配置项（可通过 `infra/.env` 覆盖）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `AI_STREAM_IN_1H` | `md.features.1h` | Layer 1 输入流 |
| `MAX_GROSS_TRENDING_HIGH` | `0.65` | TRENDING 高置信度上限 |
| `MAX_GROSS_TRENDING_MID` | `0.45` | TRENDING 中置信度上限 |
| `MAX_GROSS_SIDEWAYS` | `0.00` | 横盘强制空仓 |
| `MAX_GROSS_VOLATILE` | `0.20` | 高波动上限 |
| `MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD` | `0.70` | 切换 HIGH/MID 的置信度阈值 |
| `AI_SMOOTH_ALPHA_MID` | `0.40` | 中置信度 EWMA alpha |
| `AI_SMOOTH_ALPHA_HIGH` | `0.60` | 高置信度 EWMA alpha |
| `MAX_TRADES_PER_DAY` | `30` | 日交易上限 |
| `MIN_NOTIONAL_USD` | `50.0` | 最小名义值过滤 |
| `MD_PRICE_HISTORY_MINUTES` | `310` | 价格历史窗口（分钟） |

---

### 6. 测试覆盖

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|----------|
| `tests/test_schemas_1h.py` | 4 | FeatureSnapshot1h 序列化、DirectionBias 约束 |
| `tests/test_market_data_1h.py` | 3 | rsi_from_hist、compute_1h_features 键、上升趋势检测 |
| `tests/test_ai_decision_layer1.py` | 5 | parse_direction_bias、is_direction_bias_valid |
| `tests/test_ai_decision_layer2.py` | 5 | apply_direction_confirmation（FLAT/LONG/SHORT/SIDEWAYS） |
| `tests/test_ai_decision_capital.py` | 8 | select_max_gross、select_smooth_alpha、apply_min_notional |

**总计：25 个新测试，全部通过。合并后整体 138 tests passed。**

---

## 运维指令

```bash
# 运行回测（需 Redis 有历史数据）
./backtester/run_backtest.sh --days 3

# 监控方向偏差
redis-cli get latest.direction_bias | python3 -m json.tool

# 监控日交易次数
redis-cli get "daily_trade_count:$(date +%Y-%m-%d)"

# 对比两个窗口的信号质量
./backtester/run_backtest.sh --compare 3 14
```

**上线条件：** 回测胜率 ≥ 47% 后部署 ai_decision。
