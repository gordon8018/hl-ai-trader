# V9改进设计文档

**日期：** 2026-03-17
**基于分析：** trade_history.csv（3/3–3/17，5342笔交易）
**目标：** 将日净PnL从 -0.7 USDC 推向盈利，核心路径是降换手率+收紧ALT配置+提升盈亏比

---

## 背景与问题诊断

### V8上线后表现（3/15–3/17稳定阶段）

| 指标 | V8稳定期 | 目标 |
|------|---------|------|
| 日均净PnL | -0.7 USDC | >0 |
| 胜率 | 43–45% | 48%+ |
| 日均手续费 | $1.16 | <$0.5 |
| Profit Factor | 1.44 | >1.85 |

### 三大核心问题

1. **手续费侵蚀**：3/16已实现PnL=+0.49，但手续费-1.16，净亏-0.68。手续费是盈利的2.4倍。
2. **ADA/DOGE持续亏损**：V8后ADA亏-1.26，DOGE亏-0.38，BTC/ETH/SOL均微盈。
3. **盈亏比不足**：当前PF=1.44，胜率45%时需PF>1.82才能盈利，差距明显。

**3/15异常**（单日1137笔）：dual-layer上线初期Layer1/2协调bug，已通过系列commits修复，但需防止复发。

---

## 架构改动一：JSON配置系统

### 设计

**文件路径：** `config/trading_params.json`

**结构：**
```json
{
  "active_version": "V9",
  "versions": {
    "V8": {
      "_description": "2026-03-15上线，基于Mar10-15回测优化",
      ...V8全量参数
    },
    "V9": {
      "_description": "2026-03-17改进版，降换手+收紧ALT+提升盈亏比",
      ...V9全量参数
    }
  }
}
```

**加载机制：**
- `app.py` 模块顶部新增 `load_config()` 函数，读取 `active_version` 对应的参数块
- 所有 `os.environ.get("PARAM", "default")` 调用替换为直接读取全局常量
- 变量名保持不变，只改赋值方式
- 不保留环境变量加载能力，完全替换为JSON加载
- `config/trading_params.json` 加入 `.gitignore`（含真实API密钥等敏感项时）

**切换版本：** 修改 `active_version` 字段，重启服务生效。

---

## 架构改动二：V9参数调整

| 参数 | V8 | V9 | 理由 |
|------|----|----|------|
| `AI_TURNOVER_CAP` | 0.05 | 0.03 | 降换手是最高ROI改动 |
| `AI_TURNOVER_CAP_HIGH` | 0.20 | 0.12 | 高置信度也需收紧 |
| `AI_MIN_MAJOR_INTERVAL_MIN` | 30 | 45 | 每小时最多1次大调仓 |
| `AI_SIGNAL_DELTA_THRESHOLD` | 0.15 | 0.20 | 过滤更多噪声信号 |
| `POSITION_PROFIT_TARGET_BPS` | 15.0 | 25.0 | 提升盈亏比（目标PF>1.85） |
| `CAP_ALT` | 0.15 | 0.08 | ADA/DOGE持续亏损 |
| `AI_MIN_CONFIDENCE` | 0.50 | 0.60 | 提高入场门槛 |
| `MAX_GROSS` | 0.35 | 0.30 | 小幅收紧基础敞口 |
| `DAILY_DRAWDOWN_HALT_USD` | 3.0 | 2.0 | 更早熔断 |
| `COOLDOWN_MINUTES` | 15 | 20 | 延长惩罚后冷静期 |

其余V8参数在V9中保持不变。

---

## 架构改动三：逻辑改进（代码层）

### 改动1：做空信号诊断日志

**位置：** `apply_direction_confirmation()`

**内容：** 每次15min周期输出结构化日志，包含：
- 当前regime分类（EMERGENCY/DEFENSIVE/BEARISH/TRENDING/NEUTRAL）
- bearish信号计数（0–5）
- 是否触发BEARISH block
- 做空条件满足情况

**目的：** 当前无法判断BEARISH regime是否曾被触发，有日志才能决定是否需要调整阈值。

### 改动2：持仓盈亏比追踪（主动止盈）

**位置：** `should_rebalance()` 内的持仓评估逻辑

**逻辑：** 将现有"超时才平仓"改为"盈利目标或超时二选一先到先平"：
```
if position_age_min >= POSITION_MAX_AGE_MIN:
    → 强制再评估（现有逻辑）
elif position_unrealized_pnl_bps >= POSITION_PROFIT_TARGET_BPS:
    → 主动触发平仓（新增）
```

**依赖：** 需要持仓浮动PnL数据，确认 `build_user_payload()` 已包含此字段（或从portfolio_state服务获取）。

### 改动3：Layer1触发频率保护（去抖动）

**位置：** `process_1h_message()` 入口

**逻辑：** 增加检查：
```python
if minutes_since_last_layer1 < 55:
    logger.info("Layer1 debounce: skip (last=%.1f min ago)", minutes_since_last_layer1)
    return
```

**目的：** 防止3/15类似事故，Layer1在55分钟内不重复触发，即使1H stream推送了新消息。

---

## 实现顺序

1. 实现JSON配置加载系统（`load_config()`），生成包含V8/V9全量参数的 `config/trading_params.json`
2. 替换 `app.py` 中所有 `os.environ.get()` 调用
3. 写入V9参数值
4. 实现3项逻辑改进
5. 更新测试（确保config加载被覆盖）
6. 验证：运行现有测试套件，确保无回归

---

## 成功标准

- 所有现有测试通过（138 tests）
- 日均换手率（按交易笔数估算）降低30%以上
- 3/16类似日的净PnL转正（手续费 < 已实现PnL）
