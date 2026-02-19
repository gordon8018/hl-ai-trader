# Replay & Backtest（事件驱动回放与离线评估）

本文件定义如何在本系统中进行离线回测与策略评估。

> 注意：
> 本系统不是“基于 K 线的回测器”，而是**生产系统的确定性重放（deterministic replay）**。
> 换句话说，我们回放“真实曾经发生的系统输入”，而不是构造价格序列。

这是 AI 交易系统能持续改进的核心能力。

## 1. 为什么必须 Replay

传统回测的问题：

- K 线回测无法模拟执行
- 无法验证风控
- 无法复现线上异常
- 无法调试 LLM 输出

而本系统所有关键决策来自：

- md.features.1m
- state.snapshot
- AI 输出
- 风控裁剪
- 执行路径

这些全部已经存在 Redis Streams 中。

因此：

`Redis Streams = 可回放的交易录像带`

Replay 的目标：

1. 重现 AI 在当时“看到的信息”
2. 重算决策
3. 比较实际 vs 新策略
4. 评估 PnL、风险与稳定性

## 2. Replay 架构

我们不会直接在生产 Redis 上回放，必须使用隔离环境：

```text
Production Redis --> Export --> Replay Redis
↓
Replay Runner
↓
ai_decision → risk_engine → execution(sim)
↓
PnL Evaluator
```

关键原则：

- Replay 不接交易所
- Replay execution 必须是 `SIMULATED`（不下单）

## 3. 需要回放的 Streams

最小集：

| Stream | 必须 | 用途 |
| --- | --- | --- |
| md.features.1m | ✓ | AI 输入 |
| state.snapshot | ✓ | 当前仓位/权益 |
| exec.reports | ✓ | 填充成交信息（用于 PnL） |

可选增强：

| Stream | 用途 |
| --- | --- |
| state.events | 更精确订单生命周期 |
| audit.logs | 诊断与调试 |
| risk.approved | 比较旧风控 vs 新风控 |

不要回放：

- exec.orders（防止重复执行逻辑污染）
- ctl.commands（除非专门测试）

## 4. 数据导出

### 4.1 导出时间范围

建议：

- 最少 24 小时
- 理想 3~7 天
- 必须包含波动行情

### 4.2 导出方式

使用 `XRANGE`：

```text
XRANGE md.features.1m start_ts end_ts
XRANGE state.snapshot start_ts end_ts
XRANGE exec.reports start_ts end_ts
```

保存为：

```text
data/replay/md.features.1m.jsonl
data/replay/state.snapshot.jsonl
data/replay/exec.reports.jsonl
```

格式：一行一个 JSON。

```json
{"stream":"md.features.1m","id":"1700000000-1","payload":{...}}
```

## 5. Replay Runner

Replay Runner 是一个“虚拟交易所时间推进器”，必须按**时间顺序**将历史事件重新写入 Replay Redis。

### 5.1 核心逻辑

伪代码：

```python
events = merge_sort_by_timestamp(all_stream_files)

for event in events:
    sleep(scale * delta_t)
    xadd(replay_stream, event.payload)
```

`scale`：

| scale | 含义 |
| --- | --- |
| 0 | 极限速度（推荐调试） |
| 0.01 | 加速回放 |
| 1 | 实时回放 |

## 6. Simulated Execution（关键）

Replay 中 execution 不再连接 Hyperliquid，而是变成**成交模拟器**。

### 6.1 成交模拟模型（MVP）

当 exec.orders 出现：

**LIMIT BUY**

- 若：`limit_px >= mid_px`，则立即成交
- 否则：等待未来 `mid_px` 穿过 `limit_px` 时成交

**LIMIT SELL**

- 若：`limit_px <= mid_px`，则立即成交
- 否则：等待未来 `mid_px` 穿过 `limit_px` 时成交

成交价格：

```text
fill_px = mid_px
```

（后续可加 spread/slippage 模型）

### 6.2 超时订单

- 若订单在 `timeout_s` 内未成交：触发 `CANCELED`
- 必须写入 `exec.reports`（模拟真实执行层行为）

## 7. PnL 计算

### 7.1 持仓计算

从 `state.snapshot` 初始化：

```text
positions[symbol].qty
equity_usd
```

然后根据 `exec.reports(FILLED)` 更新：

```text
qty += signed_fill_qty
cash -= fill_qty * fill_px
```

### 7.2 Mark-to-Market

每个 `md.features.1m`：

```text
equity = cash + Σ(qty * mid_px)
```

记录：

- equity_curve
- drawdown
- turnover

## 8. 评估指标

### 8.1 收益

- 总收益率
- 每分钟收益分布
- Sharpe（minute）
- 信息比率

### 8.2 风险

- 最大回撤（MDD）
- 平均回撤
- 回撤持续时间

### 8.3 交易质量

- 平均换手率
- 成交率
- 订单取消率
- 持仓稳定性

### 8.4 AI 质量

- LLM fallback 比例
- confidence 与收益相关性
- 高 confidence 时的收益分布

## 9. 对比回测（最重要用途）

Replay 的核心不是“看赚钱”，而是：

> 比较两个策略版本。

流程：

```text
baseline replay -> 生成 equity_A
new AI replay -> 生成 equity_B
比较 A vs B
```

关键对比：

- 相同市场
- 相同执行
- 唯一变化：AI

这才是有效的策略评估。

## 10. 常见陷阱

| 问题 | 原因 |
| --- | --- |
| 回测很好实盘差 | 忽略执行延迟 |
| PnL 抖动大 | 未使用 state.snapshot 初始化 |
| 结果不稳定 | Replay 时间顺序错误 |
| AI 表现波动 | 未固定模型/temperature |

## 11. 强制要求

在允许任何 AI 策略上线主网前，必须通过：

- ≥ 3 天 replay
- 无异常换手
- 无异常回撤
- 优于 baseline

否则禁止实盘。

## 12. 结论

Replay 是 AI 交易系统的“单元测试”。

没有 replay，
你不是在开发交易系统，
你是在赌博。
