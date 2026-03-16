# 双层决策架构改进设计

**日期：** 2026-03-16
**状态：** 已批准，待实施
**背景：** 基于 2026/3/3–3/16 实盘交易数据分析（5,295 笔，净亏损 -$91.5 USDT）

---

## 问题诊断

| 问题 | 数据依据 |
|------|---------|
| 交易过于频繁 | v8 期间每小时 168 笔，手续费/毛PnL 比高达 142% |
| 信号质量不足 | 全期胜率 34.2%，盈亏平衡需要 51.6%，差距 -17.4% |
| 资金利用率极低 | 实际部署资本 <2%，EWMA α=0.25 导致建仓极慢 |
| 无回测验证 | 所有参数调整直接在实盘验证，历次调参（v2–v8）均未突破 40% 胜率 |

---

## 方案概述：方案 B — Prompt 重构 + 双层决策 + 轻量回测

### 约束条件

- 账户规模：$500–$2,000
- 目标交易频率：每日 ≤30 笔
- 决策引擎：LLM 主导（保留现有架构）
- 改动范围：`market_data`、`ai_decision`、`shared/schemas`、`infra/.env`、新增 `backtester/`

---

## 第一节：架构总览

```
┌─────────────────────────────────────────────┐
│  NEW: backtester/                            │
│   ├── replay.py      (历史数据回放)           │
│   ├── evaluator.py   (胜率/盈亏比计算)        │
│   └── run_backtest.sh (CLI 入口)             │
├─────────────────────────────────────────────┤
│  MODIFIED: services/ai_decision/app.py       │
│   ├── Layer 1: 1H LLM 方向决策               │
│   ├── Layer 2: 15m 规则确认入场              │
│   ├── 资金利用率改进（动态 EWMA + MAX_GROSS） │
│   └── 新 prompt（市场状态过滤 + 3+ 信号门槛）  │
├─────────────────────────────────────────────┤
│  MODIFIED: services/market_data/app.py       │
│   └── 新增 md.features.1h 流                │
│       （ret_4h / vol_4h / trend_4h / RSI_1h）│
├─────────────────────────────────────────────┤
│  MODIFIED: shared/schemas.py                 │
│   └── 新增 DirectionBias schema              │
├─────────────────────────────────────────────┤
│  MODIFIED: infra/.env                        │
│   └── 参数更新（见第四节）                    │
└─────────────────────────────────────────────┘
```

数据流不变。回测模块独立运行，不接入实盘 Redis。

---

## 第二节：Prompt 重构

### 新增 Step -1：市场状态前置过滤器（SIDEWAYS 强制空仓）

满足以下任意 2 条 → `market_state = SIDEWAYS` → 强制 `cash_weight = 1.0`，不得开仓：

- `|ret_1h| < 0.3%`（价格1小时内几乎不动）
- `vol_regime == 0 AND trend_agree == 0`（低波动 + 趋势不一致）
- `|book_imbalance_l10| < 0.05`（买卖盘极度均衡）
- `aggr_delta_5m` 近 5 根 K 线符号交替（无持续方向）

### 修改 Step 2：信号确认门槛 2+ → 3+

必须满足 3 个及以上（原为 2 个）：
- `funding_rate` 方向一致
- `basis_bps` 方向一致
- `oi_change_15m` 正向
- `book_imbalance_l5 > 0.10`
- `aggr_delta_5m` 同向
- `trend_strength_15m > 0.60`

### 新增 Step 2.5：频率自我约束

在 user payload 中注入 `minutes_since_last_trade`，prompt 规则：

```
若 minutes_since_last_trade < 30 AND confidence < 0.70 → decision_action = "HOLD"
```

### 置信度门槛调整

| 场景 | 原门槛 | 新门槛 |
|------|-------|-------|
| 开新仓 | 0.50 | **0.65** |
| 加仓 | 0.50 | **0.70** |
| 高置信路径 | 0.75 | **0.80** |

---

## 第三节：双层决策结构

### Layer 1：1H 方向层（LLM）

**触发条件：** 每整点小时，读取 `md.features.1h`

**输出：** `alpha.direction_1h` → Redis key `latest.direction_bias`（有效期 60 分钟）

```json
{
  "biases": [
    {"symbol": "BTC", "direction": "LONG",  "confidence": 0.72},
    {"symbol": "ETH", "direction": "FLAT",  "confidence": 0.55},
    {"symbol": "SOL", "direction": "SHORT", "confidence": 0.68},
    {"symbol": "ADA", "direction": "FLAT",  "confidence": 0.40},
    {"symbol": "DOGE","direction": "FLAT",  "confidence": 0.35}
  ],
  "market_state": "TRENDING",
  "valid_until_minute": "2026-03-16T15:00Z"
}
```

**FLAT 输出条件**（满足任意 2 条）：
- `|ret_1h| < 0.3%` 且 `trend_agree == 0`
- `vol_regime == 0`（低波动）
- `RSI_1h` 在 45–55 区间
- `aggr_delta_1h` 符号与 `trend_1h` 相反

### Layer 2：15m 确认层（规则引擎，无 LLM）

**触发条件：** 每 15 分钟，读取 `md.features.15m` + `latest.direction_bias`

```python
direction = get_direction_bias(symbol)   # 从 Redis 读

if direction == "FLAT":
    return weight = 0                    # 直接 HOLD

confirm_signals = sum([
    funding_rate > 0 and direction == "LONG",
    basis_bps > 0 and direction == "LONG",
    oi_change_15m > 0,
    book_imbalance_l5 > 0.10 and direction == "LONG",
    aggr_delta_5m > 0 and direction == "LONG",
    trend_strength_15m > 0.6,
])

if confirm_signals >= 3:
    execute(direction)
else:
    hold()
```

### market_data 新增 1H 特征

| 新增特征 | 说明 |
|---------|------|
| `ret_4h` | 4小时收益率 |
| `vol_4h` | 4小时波动率 |
| `trend_4h` | 4小时趋势方向 |
| `RSI_1h` | 1小时 RSI(14) |
| `aggr_delta_1h` | 1小时累积订单流方向 |

---

## 第四节：频率硬控制与参数更新

### 新增控制点

**每日交易上限（Redis 计数器）**
```python
DAILY_TRADE_CAP = int(os.getenv("MAX_TRADES_PER_DAY", "30"))
daily_trades = redis.incr(f"daily_trade_count:{today_utc}")
if daily_trades > DAILY_TRADE_CAP:
    return HOLD
```

**minutes_since_last_trade 注入 payload**
```python
last_trade_ts = redis.get("last_trade_timestamp")
minutes_since = (now - last_trade_ts) / 60 if last_trade_ts else 9999
payload["minutes_since_last_trade"] = round(minutes_since, 1)
```

### 环境变量更新（infra/.env）

| 参数 | 原值 | 新值 |
|------|------|------|
| `AI_MIN_MAJOR_INTERVAL_MIN` | 15 | **60** |
| `POSITION_MAX_AGE_MIN` | 30 | **120** |
| `MAX_TRADES_PER_DAY` | — | **30** |
| `AI_TURNOVER_CAP` | 0.05 | **0.20** |

### 预期频率效果

| 指标 | v8 现状 | 改后预期 |
|------|--------|---------|
| LLM 调用次数/天 | ~96 | **24** |
| 实际下单笔数/天 | ~400 | **≤30** |
| 每日手续费 | ~$2.4 | **≤$0.18** |
| 盈亏平衡胜率需求 | 51.6% | **~47%** |

---

## 第五节：资金利用率改进

### 分模式 MAX_GROSS

| 市场状态 | 1H 置信度 | 原 MAX_GROSS | 新 MAX_GROSS |
|---------|----------|-------------|-------------|
| TRENDING | ≥ 0.70 | 0.35 | **0.65** |
| TRENDING | 0.50–0.70 | 0.35 | **0.45** |
| SIDEWAYS | 任意 | 0.35 | **0.00** |
| VOLATILE | 任意 | 0.35 | **0.20** |

### 单币种仓位上限放宽

| 币种 | 原上限 | 新上限（TRENDING 高置信） |
|------|-------|----------------------|
| BTC/ETH | 0.30 | **0.45** |
| SOL/ADA/DOGE | 0.15 | **0.25** |

### 动态 EWMA Alpha

```python
if confidence >= 0.70:
    smooth_alpha = 0.60   # 1 个周期到达 60% 目标
elif confidence >= 0.55:
    smooth_alpha = 0.40
else:
    smooth_alpha = 0.25   # 保守默认
```

### 最小名义仓位保护

```python
MIN_NOTIONAL_USD = 50   # 低于 $50 不执行，避免手续费侵蚀
```

### 预期资金利用率（$1,000 账户）

```
当前：BTC 目标 0.30 → EWMA α=0.25 → 第1周期实际 $75
改后：BTC 目标 0.45 → EWMA α=0.60 → 第1周期实际 $270
资金利用率：从 ~2% 提升至 ~25–40%（仅在高质量信号时）
```

---

## 第六节：回测模块

### 文件结构

```
backtester/
  ├── replay.py       # 历史数据回放（读 reporting.db + md_features）
  ├── evaluator.py    # 胜率/盈亏比/手续费分析
  └── run_backtest.sh # CLI 入口
```

### 评估逻辑

1. 逐条读取历史 15m 特征快照，构造与实盘相同的 `user_payload`
2. 调用 LLM 或 dry-run 规则模拟，获取 `DirectionBias` + `TargetPortfolio`
3. 用下一根 15m 收益率判断方向正确性
4. 输出按市场状态分类的胜率、盈亏比、预期净PnL

### CLI 用法

```bash
./backtester/run_backtest.sh --days 7 --dry-run        # 不调 LLM
./backtester/run_backtest.sh --days 7 --use-llm        # 调用真实 LLM
./backtester/run_backtest.sh --compare v7 v8_new       # 对比两个参数版本
```

### 工作流

```
改 prompt / 调参 → 运行回测 → 胜率 > 50% → 部署实盘
                              ↓ 胜率 < 50%
                         继续调整，不上实盘
```

---

## 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `services/market_data/app.py` | 修改 | 新增 `md.features.1h` 流及 1H 特征计算 |
| `services/ai_decision/app.py` | 修改 | 双层决策循环、新 prompt、动态 EWMA、资金利用率 |
| `shared/schemas.py` | 修改 | 新增 `DirectionBias`、`FeatureSnapshot1h` schema |
| `infra/.env` | 修改 | 参数更新（见第四节） |
| `backtester/replay.py` | 新增 | 历史数据回放 |
| `backtester/evaluator.py` | 新增 | 评估指标计算 |
| `backtester/run_backtest.sh` | 新增 | CLI 入口 |
