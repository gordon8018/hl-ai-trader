# 服务：ai_decision（核心 AI 决策）

## 1. 职责
- 每分钟读取市场特征与当前组合状态
- 生成 TargetPortfolio（目标权重/仓位）
- 保证输出稳定、低换手、可审计
- LLM 失败自动降级 baseline

## 2. 输入
- Stream: md.features.1m（FeatureSnapshot1m）
- Redis key: latest.state.snapshot（StateSnapshot）

## 3. 输出
- Stream: alpha.target（TargetPortfolio）
- Redis key: latest.alpha.target（用于平滑）
- audit.logs：必须写（used、confidence、turnover、gross、raw_llm）

## 4. LLM 输出要求
- 必须输出严格 JSON（targets/cash_weight/confidence/rationale）
- 不允许输出 prose；解析失败则降级 baseline

## 5. 稳定器（必须实现）
1) Pydantic 校验 + schema 校验
2) cap & scale：per-symbol cap、gross/net cap
3) confidence gating：confidence < threshold → gross 缩放
4) EWMA smoothing：w = (1-a)*prev + a*new
5) turnover cap：相对 current_weights，若换手超限则缩放 delta

## 6. Baseline（降级策略）
- mom/vol scaling：
  - raw = max(ret_5m, 0) / vol_1h
  - normalize to MAX_GROSS
- rationale 记录 used=baseline_fallback 与 error

## 7. 质量/验收
- 每分钟产出 alpha.target
- gross <= MAX_GROSS，net <= MAX_NET
- turnover <= TURNOVER_CAP（允许轻微浮动但需记录）
