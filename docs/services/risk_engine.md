# 服务：risk_engine（风控网关）

## 1. 职责
- 将 TargetPortfolio 裁剪为 ApprovedTargetPortfolio
- 输出 mode：NORMAL / REDUCE_ONLY / HALT
- 所有裁剪必须可解释（rejections）

## 2. 输入
- Stream: alpha.target
- Redis key: latest.state.snapshot
- （可选）md.features.1m（用于流动性/波动异常判断）

## 3. 输出
- Stream: risk.approved
- audit.logs：risk_summary、rejections、mode

## 4. 风控规则（MVP）
- per-symbol cap（BTC/ETH 与 ALT 分开）
- gross cap：sum(abs(w)) <= MAX_GROSS
- net cap：abs(sum(w)) <= MAX_NET
- turnover cap：相对当前权重 sum(abs(Δw)) <= TURNOVER_CAP
- 异常：无 state.snapshot/对账失败 → HALT

## 5. 模式
- NORMAL：允许加减仓
- REDUCE_ONLY：只允许降低绝对暴露（执行层必须遵守）
- HALT：禁止交易（执行层不下单）

## 6. 质量/验收
- risk.approved 每分钟输出
- rejections 说明原因
- mode 切换必须写 audit.logs
