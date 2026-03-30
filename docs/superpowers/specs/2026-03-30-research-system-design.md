# Research System Design — 因子挖掘与策略优化

**日期:** 2026-03-30
**状态:** Approved

## 背景

hl-ai-trader 是一个基于 Hyperliquid 永续合约的微服务交易系统，拥有 100+ 因子特征、双层决策引擎（LLM + 规则）、完整的风控和执行链路。当前盈利效果不佳，主要瓶颈：

1. **信号质量不明** — 100+ 因子缺乏系统化评估，不知道哪些真正有预测力
2. **参数调优盲目** — V8→V9 的参数调整缺乏数据支撑
3. **回测能力不足** — 现有 `backtester/replay.py` 功能有限

## 目标

构建一套完全独立于线上系统的 research 系统，实现：
- 系统化因子评估与筛选
- 事件驱动回测与策略验证
- LLM 驱动的参数优化搜索

渐进式交付：先 CLI 工具链快速出成果，后续演进为平台。

## 总体架构

```
research/                          # 完全独立于线上交易系统
├── data/                          # 数据层
│   ├── exporter.py                # Redis Streams + SQLite → Parquet
│   ├── parquet/                   # 按日期分区的 Parquet 文件
│   │   ├── features_1m/
│   │   ├── features_15m/
│   │   ├── features_1h/
│   │   ├── trades/
│   │   └── snapshots/
│   └── duckdb/
│       └── research.duckdb        # 查询层，外部表挂载 Parquet
│
├── factors/                       # 因子评估层
│   ├── registry.py                # 因子注册表（名称、分组、计算函数）
│   ├── evaluator.py               # 单因子 IC/IR/衰减/分组收益分析
│   ├── correlation.py             # 因子相关性矩阵、冗余检测
│   └── report.py                  # 因子评估报告生成
│
├── backtest/                      # 回测引擎
│   ├── engine.py                  # 事件驱动回测（逐 bar 回放）
│   ├── cost_model.py              # 交易成本模型（手续费+滑点+funding）
│   ├── scorer.py                  # 综合评分体系
│   └── report.py                  # 回测报告
│
├── optimizer/                     # 参数优化（复用 feature_factory）
│   ├── search_space.py            # 加密因子搜索空间定义
│   ├── param_generator.py         # LLM 驱动参数生成（复用）
│   ├── executor.py                # 批量实验执行器（复用）
│   ├── results_store.py           # 结果存储+饱和度检测（复用）
│   └── evaluator.py               # 实验评估（适配）
│
├── scripts/                       # CLI 入口
│   ├── export_data.py             # 一键导出历史数据
│   ├── eval_factors.py            # 一键因子评估
│   ├── run_backtest.py            # 一键回测
│   └── search_params.py           # 启动参数搜索
│
└── notebooks/                     # Phase 1 探索性分析（可选）
    └── factor_exploration.ipynb
```

### 设计原则

- 与线上系统完全隔离，通过数据导出衔接
- 复用 `shared/schemas.py` 的 FeatureSnapshot 定义保持字段一致
- Parquet 做存储，DuckDB 做查询，无外部数据库依赖
- `optimizer/` 从 `feature_factory/analysis/agent/` 移植核心框架

## 模块详细设计

### 1. 数据导出层 (`data/`)

**数据源映射：**

| 数据源 | 导出目标 | 分区 | 单日数据量 |
|--------|---------|------|-----------|
| `md.features.1m` (Redis) | `parquet/features_1m/{date}.parquet` | 按日 | ~2880 行 |
| `md.features.15m` (Redis) | `parquet/features_15m/{date}.parquet` | 按日 | ~192 行 |
| `md.features.1h` (Redis) | `parquet/features_1h/{date}.parquet` | 按日 | ~48 行 |
| `exec_reports` (SQLite) | `parquet/trades/{date}.parquet` | 按日 | ~15-30 行 |
| `state_snapshots` (SQLite) | `parquet/snapshots/{date}.parquet` | 按日 | ~24 行 |

**核心类：**

```python
class DataExporter:
    def export_redis_stream(self, stream_key, schema_cls, date_range):
        """Redis XRANGE → 按 FeatureSnapshot schema 解析 → Parquet"""
        # 增量导出：检查已有 parquet，只导出缺失日期

    def export_sqlite_table(self, table, date_range):
        """SQLite 查询 → Parquet"""

    def build_forward_returns(self, features_df, horizons=[1,2,4,8,16]):
        """计算前瞻收益列：ret_fwd_15m ... ret_fwd_4h (基于 15m bar)"""
```

**DuckDB 查询层：** 外部表挂载 Parquet，零拷贝查询。

**数据形态转换：**

Redis Streams 中的 FeatureSnapshot 使用 `Dict[str, float]` 存储每个因子（如 `{"BTC": 0.003, "ETH": -0.001}`）。导出时必须 pivot 为标准面板数据：**每行一个 (timestamp, symbol) 对**，每个因子为标量列。

```
导出前 (Redis):  ts=T, book_imbalance_l5={"BTC": 0.12, "ETH": -0.05}
导出后 (Parquet):
  ts=T, symbol=BTC, book_imbalance_l5=0.12, ...
  ts=T, symbol=ETH, book_imbalance_l5=-0.05, ...
```

**前瞻收益计算：**

horizons 对齐实际交易时间尺度（持仓 15-120 分钟）：
- 基于 15m bar: `[1, 2, 4, 8, 16]` bars（对应 15min, 30min, 1h, 2h, 4h）
- 命名: `ret_fwd_15m`, `ret_fwd_30m`, `ret_fwd_1h`, `ret_fwd_2h`, `ret_fwd_4h`

**Look-ahead bias 防护：**
- 前瞻收益仅用于因子评估的 target，不参与因子计算
- 导出时保留 Redis Stream 的消息 ID 顺序，确保时序一致
- NaN 值（如开盘前/收盘后缺失 bar）标记为 null，因子评估时跳过

**数据量要求：**
- 最低要求：60 天历史数据（约 11,520 个 15m bars）
- 若当前数据不足，Phase 1 先启动数据采集，积累到位后再运行因子评估

**其他关键决策：**
- 增量导出避免重复处理
- Schema 从 `shared/schemas.py` 继承

### 2. 因子评估层 (`factors/`)

**因子分组注册表：**

每个因子标注来源 snapshot 时间粒度（1m/15m/1h），避免取值时 KeyError。

```python
FACTOR_FAMILIES = {
    "microstructure": [  # 来源: 1m/15m
        "book_imbalance_l1",        # 1m, 15m
        "book_imbalance_l5",        # 1m, 15m
        "book_imbalance_l10",       # 1m, 15m
        "book_imbalance",           # 1m (legacy)
        "spread_bps",               # 1m, 15m
        "liquidity_score",          # 1m, 15m
        "top_depth_usd",            # 1m, 15m
        "top_depth_usd_l10",        # 15m
    ],
    "microstructure_dynamics": [  # 来源: 1m
        "microprice_change_1m",     # 1m
        "book_imbalance_change_1m", # 1m
        "spread_change_1m",         # 1m
        "depth_change_1m",          # 1m
        "bid_slope",                # 1m
        "ask_slope",                # 1m
        "queue_imbalance_l1",       # 1m
    ],
    "order_flow": [  # 来源: 1m/15m
        "trade_volume_buy_ratio",   # 1m, 15m
        "aggr_delta_1m",            # 1m, 15m
        "aggr_delta_5m",            # 1m, 15m
        "volume_imbalance_1m",      # 1m, 15m
        "volume_imbalance_5m",      # 1m, 15m
        "buy_pressure_1m",          # 1m, 15m
        "sell_pressure_1m",         # 1m, 15m
        "absorption_ratio_bid",     # 1m, 15m
        "absorption_ratio_ask",     # 1m, 15m
    ],
    "momentum": [  # 来源: 15m/1h
        "ret_15m",                  # 15m
        "ret_30m",                  # 15m
        "ret_1h",                   # 15m, 1h
        "ret_4h",                   # 15m, 1h
        "ret_24h",                  # 15m
        "trend_15m",                # 15m
        "trend_1h",                 # 15m, 1h
        "trend_4h",                 # 15m, 1h
        "trend_agree",              # 15m, 1h
        "trend_strength_15m",       # 15m
        "rsi_14_1m",                # 15m (名称有误导，实际在15m snapshot)
    ],
    "volatility": [  # 来源: 15m/1h
        "vol_15m",                  # 15m
        "vol_1h",                   # 15m, 1h
        "vol_4h",                   # 15m, 1h
        "vol_spike",                # 15m
        "vol_regime",               # 15m, 1h
        "vol_15m_p90",              # 15m
    ],
    "regime": [  # 来源: 15m
        "liq_regime",               # 15m
        "liquidity_drop",           # 15m
        "top_depth_usd_p10",        # 15m
    ],
    "funding_basis": [  # 来源: 15m/1h
        "funding_rate",             # 15m, 1h
        "basis_bps",                # 15m, 1h
        "oi_change_15m",            # 15m
    ],
    "cross_market": [  # 来源: 1m/15m
        "btc_ret_1m",               # 1m, 15m
        "btc_ret_15m",              # 15m
        "eth_ret_1m",               # 1m, 15m
        "eth_ret_15m",              # 15m
        "corr_btc_1h",              # 15m
        "corr_eth_1h",              # 15m
        "market_ret_mean_1m",       # 1m, 15m
        "market_ret_std_1m",        # 1m, 15m
    ],
    "execution_feedback": [  # 来源: 15m
        "reject_rate_15m",          # 15m
        "slippage_bps_15m",         # 15m
        "p95_latency_ms_15m",       # 15m
    ],
}
```

**单因子评估指标：**

| 指标 | 含义 | 计算方式 |
|------|------|---------|
| IC | 因子值与前瞻收益的相关性 | `corr(factor, ret_fwd_N)` per bar |
| IC_mean | IC 的时序均值 | 越大说明预测力越稳 |
| ICIR | IC 稳定性 | `IC_mean / IC_std` |
| IC 衰减曲线 | 预测力随时间的衰减 | IC_mean @ horizon 15m/30m/1h/2h/4h |
| 分组收益 | 因子分 5 组后各组前瞻收益 | 最高组-最低组的收益差 |
| 单调性 | 分组收益是否单调 | Spearman rank of group returns |
| 换手率 | 因子值的自相关性 | `corr(factor_t, factor_{t-1})` |

**筛选标准（初始阈值，可调）：**
- `|ICIR| > 0.5` → 有效因子
- `|ICIR| < 0.2` → 噪声因子，建议剔除
- 因子间 `|corr| > 0.8` → 冗余，保留 ICIR 更高的

**评估流程：**

```
对每个因子 × 每个前瞻周期:
  1. 计算逐 bar 的 IC → IC 时序
  2. 统计 IC_mean, IC_std, ICIR
  3. 按因子值分 5 组，计算各组平均前瞻收益
  4. 检查单调性
  5. 按 symbol 分别计算（BTC vs ETH 可能差异大）
```

### 3. 回测引擎 (`backtest/`)

**事件驱动回测核心：**

```python
class BacktestEngine:
    def run(self, strategy, data, cost_model, initial_capital=10000):
        for bar in data.iter_bars():
            target = strategy.on_bar(bar)
            trades = self.diff_positions(target, current_positions)
            costs = cost_model.calculate(trades, bar)
            self.update(trades, costs, bar)
        return self.build_result()
```

**交易成本模型：**

```python
class CryptoPerpCostModel:
    taker_fee_bps: float = 3.5       # Hyperliquid taker fee (可按账户等级调整)
    maker_fee_bps: float = 1.0
    slippage_base_bps: float = 1.0   # 基础滑点
    funding_interval_hours: int = 8

    def calculate(self, trades, bar):
        fee = notional * self.taker_fee_bps / 10000
        # 深度感知滑点: slippage = base + notional / top_depth * impact_coeff
        slippage = notional * (self.slippage_base_bps + notional / bar.top_depth_usd * 100) / 10000
        return fee + slippage

    def calculate_funding(self, positions, bar):
        """每个 bar 对持仓收取/支付 funding，而非仅在交易时"""
        # funding 按 8h 结算，每个 15m bar 承担 1/32 的费率
        return sum(pos.notional * bar.funding_rate / 32 for pos in positions)
```

**可插拔策略接口：**

```python
class Strategy(Protocol):
    def on_bar(self, bar: BarData) -> dict[str, float]:
        """返回目标仓位权重 {'BTC': 0.15, 'ETH': -0.05}"""

# 内置实现
class RuleBasedStrategy:
    """复刻当前 Layer2 规则作为基线，必须包含以下有状态约束：
    - DirectionBias 缓存（模拟 Layer1 输出）
    - profit_target (25bps) / stop_loss (40bps)
    - min_trade_interval (90min)
    - max_trades_per_day (15)
    - direction_reversal_penalty (30min 窗口)
    - position_max_age (30min)
    - bearish_regime_long_block
    - PnL-based scaling & cooldown
    """
class SingleFactorStrategy:    # 单因子多空，因子有效性验证
class WeightedFactorStrategy:  # 加权多因子，组合测试
```

**综合评分体系：**

```python
class Scorer:
    metrics = {
        "sharpe":        {"weight": 0.25},
        "sortino":       {"weight": 0.15},
        "calmar":        {"weight": 0.10},
        "win_rate":      {"weight": 0.15},
        "profit_factor": {"weight": 0.15},
        "net_pnl":       {"weight": 0.10},
        "max_drawdown":  {"weight": 0.10},  # 惩罚项
    }

    def score(self, equity_curve, trades) -> float:
        """归一化后加权求和，0-100 分
        归一化方式：
        - sharpe/sortino/calmar/profit_factor: clip 到合理范围后线性映射到 [0, 100]
        - win_rate/net_pnl: 线性映射
        - max_drawdown: 反转映射 (0% DD → 100, 50% DD → 0)
        """
```

**Walk-Forward 验证：**

```
|---- Train ----|-- Test --|
       |---- Train ----|-- Test --|
              |---- Train ----|-- Test --|

滚动窗口: train=7天, test=2天, step=2天
最终指标 = 所有 test 窗口的加权平均
```

### 4. 参数优化层 (`optimizer/`)

**从 feature_factory 复用的模块：**

| 原模块 | 改动点 |
|--------|--------|
| `param_generator.py` | Prompt 模板改为加密因子语境 |
| `executor.py` | 调用目标从 `analyze_factor_pool.py` 改为 `run_backtest.py` |
| `results_store.py` | 指标字段适配（day_win_t1 → sharpe 等） |
| `auto_searcher.py` | 配置项适配 |

**搜索空间：**

```python
SEARCH_DIMENSIONS = {
    "active_factors": {
        "families": FACTOR_FAMILIES,
        "select_mode": "family",
    },
    "signal_params": {
        "confidence_threshold": (0.3, 0.8, 0.05),
        "signal_delta_threshold": (0.03, 0.15, 0.01),
        "smooth_alpha": (0.1, 0.6, 0.05),
    },
    "position_params": {
        "max_gross": (0.15, 0.50, 0.05),
        "max_net": (0.10, 0.35, 0.05),
        "turnover_cap": (0.02, 0.10, 0.01),
        "profit_target_bps": (10, 50, 5),
        "stop_loss_bps": (15, 80, 5),
    },
    "timing_params": {
        "min_trade_interval_min": (15, 120, 15),
        "position_max_age_min": (15, 120, 15),
        "max_trades_per_day": (5, 30, 5),
    },
    # Layer 1 参数（方向偏差生成）
    "layer1_params": {
        "bearish_ret_1h_threshold": (-0.01, -0.002, 0.001),
        "trending_strength_threshold": (0.5, 2.0, 0.25),
        "layer1_debounce_min": (30, 90, 15),
        "bearish_regime_long_block": [True, False],
    },
}
```

**实验评估标准：**

```python
good_thresholds = {
    "sharpe": 1.0,
    "max_drawdown": 0.10,
    "win_rate": 0.45,
    "profit_factor": 1.3,
    "net_pnl_positive": True,
}
# 至少满足 4/5 个阈值视为 "good strategy"
```

## 实施阶段

### Phase 1：数据导出 + 因子评估

- `data/exporter.py` — Redis/SQLite → Parquet 增量导出
- `data/duckdb/` — DuckDB 外部表挂载
- `factors/registry.py` — 因子注册表
- `factors/evaluator.py` — IC/ICIR/衰减/分组收益
- `factors/correlation.py` — 相关性矩阵
- `factors/report.py` — 评估报告
- `scripts/export_data.py` — CLI
- `scripts/eval_factors.py` — CLI

### Phase 2：回测引擎

- `backtest/engine.py` — 事件驱动回测
- `backtest/cost_model.py` — 交易成本模型
- `backtest/scorer.py` — 综合评分
- `backtest/report.py` — 回测报告
- `scripts/run_backtest.py` — CLI

### Phase 3：参数优化

- `optimizer/` — 从 feature_factory 移植 + 适配
- `scripts/search_params.py` — CLI

## CLI 使用示例

```bash
# Phase 1
python research/scripts/export_data.py --source redis,sqlite --date-range 2026-03-01:2026-03-30
python research/scripts/eval_factors.py --horizons 15m,30m,1h,2h,4h --output research/reports/factor_eval/

# Phase 2
python research/scripts/run_backtest.py --strategy rule_based --date-range 2026-03-01:2026-03-30
python research/scripts/run_backtest.py --strategy single_factor --factor book_imbalance_l5
python research/scripts/run_backtest.py --strategy weighted_factor --factors 'book_imbalance_l5:0.3,aggr_delta_5m:0.25' --walk-forward train=7d,test=2d

# Phase 3
python research/scripts/search_params.py --num-workers 4 --max-rounds 10 --holdout 2026-03-25:2026-03-30
```

## 产出物

```
research/reports/
├── factor_eval/
│   ├── summary.csv              # 因子排名表
│   ├── ic_decay_curves.png      # IC 衰减曲线
│   ├── correlation_matrix.png   # 因子相关性热力图
│   └── quantile_returns.png     # 分组收益图
├── backtest/
│   ├── {strategy}_{ts}/
│   │   ├── summary.json         # 综合评分 + 所有指标
│   │   ├── equity_curve.parquet
│   │   └── trades.parquet
│   └── comparison.csv           # 多策略对比表
└── optimizer/
    ├── search_results.csv       # 全部实验结果
    ├── best_params.json         # 最优参数组合
    └── scatter.png              # Sharpe vs MaxDD 散点图
```
