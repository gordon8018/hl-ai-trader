# AI Prompting 规范（分钟级组合交易系统）

本文件定义 AI 决策模块（ai_decision）的提示工程规范、结构化输出约束、反幻觉机制、稳定性策略与评估标准。

目标：让 LLM 成为“组合构建引擎”，而不是“情绪化信号生成器”。

## 1. 设计原则

### 1.1 LLM 的角色

LLM 在系统中的职责是：

- 接收：市场特征 + 当前组合状态 + 风控约束
- 输出：目标组合权重（TargetPortfolio）
- 目标：低换手、稳健、风险受控

LLM 不负责：

- 下单
- 风控
- 仓位换算
- 执行细节
- 账户状态判断

这些必须由系统层实现。

## 2. Prompt 结构设计

采用固定两段结构：

1. System Prompt（固定模板）
2. User Prompt（结构化 JSON）

### 2.1 System Prompt（必须固定）

示例：

```text
You are a portfolio construction engine for crypto perpetual futures.

Your task:

Propose minute-level portfolio weights.

Control risk and turnover.

Output STRICT JSON only.

No prose outside JSON.

Follow the output schema exactly.

Do NOT hallucinate data.
If uncertain, reduce exposure.
Prefer stability over aggressiveness.
```

约束：

- temperature ≤ 0.2
- 不允许“解释性 prose”
- 不允许 Markdown

### 2.2 User Prompt（必须 JSON）

必须包含：

- universe
- constraints
- market_features_1m
- portfolio_state
- output_schema
- notes

示例结构：

```json
{
  "task": "Propose target portfolio weights for the next minute.",
  "universe": ["BTC","ETH","SOL","ADA","DOGE"],
  "constraints": {
    "max_gross": 0.4,
    "max_net": 0.2,
    "per_symbol_caps": {
      "BTC": 0.15,
      "ETH": 0.15,
      "SOL": 0.10,
      "ADA": 0.10,
      "DOGE": 0.10
    },
    "turnover_cap_per_cycle": 0.10,
    "prefer_low_turnover": true
  },
  "market_features_1m": {
    "asof_minute": "...",
    "mid_px": {...},
    "ret_1m": {...},
    "ret_5m": {...},
    "ret_1h": {...},
    "vol_1h": {...}
  },
  "portfolio_state": {
    "equity_usd": ...,
    "positions": {...}
  },
  "output_schema": {
    "required": ["targets","cash_weight","confidence","rationale"]
  }
}
```

禁止：

- 冗长自然语言描述
- 隐式条件
- 不明确字段

## 3. 输出规范（严格 JSON）

LLM 必须输出：

```json
{
  "targets": [
    {"symbol":"BTC","weight":0.12},
    {"symbol":"ETH","weight":0.10},
    {"symbol":"SOL","weight":0.05},
    {"symbol":"ADA","weight":0.03},
    {"symbol":"DOGE","weight":0.00}
  ],
  "cash_weight": 0.70,
  "confidence": 0.64,
  "rationale": "Short explanation"
}
```

约束：

- 所有 universe 成员必须出现（缺失视为 0）
- weight 单位为“权益比例”
- weight 可为负（若支持做空）
- confidence ∈ [0,1]
- rationale ≤ 800 字

## 4. 反幻觉机制（必须实现）

LLM 不可靠，因此必须多层防护。

### 4.1 JSON 提取层

- 支持 fenced ```json
- 提取第一个 {...} 块
- 解析失败直接 fallback baseline

### 4.2 Schema 校验层

使用 Pydantic 校验：

- symbol 必须在 universe
- weight 必须为 float
- confidence 在 [0,1]

不合格 → fallback

### 4.3 约束裁剪层

即便 LLM 输出合法 JSON，仍需：

- per-symbol cap
- gross cap
- net cap
- turnover cap
- confidence gating

## 5. 稳定性策略（核心）

分钟级系统最大风险：过度换手。

### 5.1 EWMA 平滑

$$
w_{final} = (1 - \alpha) \cdot w_{prev} + \alpha \cdot w_{llm}
$$

建议：

- alpha ∈ [0.2, 0.4]
- alpha 越小越稳定

### 5.2 Turnover Cap

定义：

$$
turnover = \sum |w_{new} - w_{current}|
$$

若超过阈值：

$$
w_{adjusted} = w_{current} + (w_{new} - w_{current}) \cdot \left(\frac{cap}{turnover}\right)
$$

### 5.3 Confidence Gating

若：

$$
confidence < MIN\_CONFIDENCE
$$

则：

$$
gross \mathrel{*}= LOW\_CONF\_SCALE
$$

这避免 LLM 不确定时高杠杆。

## 6. 二阶段 AI（推荐增强）

更稳的架构：

- 阶段 1（LLM）：
  - 输出：
    - regime（BULL/NEUTRAL/DEFENSIVE）
    - risk_multiplier ∈ [0,1]
    - factor_weights（如 mom/mean-rev/vol）
- 阶段 2（Deterministic Optimizer）：
  - 根据 regime + risk_multiplier
  - 结合 baseline 因子模型
  - 求解权重

优点：

- 可解释
- 可控
- 可回测

不建议直接完全由 LLM 决定权重。

## 7. 审计要求（必须）

每次 alpha.target 必须写入 audit.logs：

字段：

- used: llm/baseline/baseline_fallback
- confidence
- turnover
- gross
- raw_llm（截断）
- final_weights

这样可以后续 replay 分析。

## 8. 评估指标（离线 & 实盘）

### 8.1 稳定性指标

- 平均 turnover
- turnover 标准差
- gross 波动率
- 信号连续性

### 8.2 收益指标

- Minute PnL
- 信息比率
- 最大回撤
- Hit ratio

### 8.3 AI 特定指标

- LLM 解析失败率
- fallback 比例
- confidence 平均值
- confidence 与收益相关性

## 9. 常见错误模式

| 错误 | 原因 | 解决 |
| --- | --- | --- |
| LLM 输出 prose | prompt 不够严格 | 强化 "STRICT JSON ONLY" |
| 频繁大幅换仓 | 未平滑 | 加强 EWMA + turnover cap |
| 置信度无意义 | 模型未理解 | 在 prompt 中明确“低置信度请降杠杆” |
| LLM 偏爱某币 | prompt 暗示偏见 | 使用对称描述 |

## 10. 推荐默认参数

| 参数 | 推荐值 |
| --- | --- |
| temperature | 0.2 |
| AI_SMOOTH_ALPHA | 0.35 |
| AI_MIN_CONFIDENCE | 0.45 |
| AI_LOW_CONF_SCALE | 0.5 |
| TURNOVER_CAP | 0.10 |

## 11. 未来增强方向

- 加入“自我一致性采样”（multiple calls → vote）
- 引入 memory（近期 regime 历史）
- 加入因子贡献解释
- RL 微调（长期）

## 12. 结论

LLM 不是 alpha 本身。
LLM 是组合决策层的“策略调节器”。

真正稳定盈利来自：

- 风控
- 执行纪律
- 换手控制
- 审计与回放
