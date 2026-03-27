# Autoresearch Risk-Adjusted Optimization Design

**Date:** 2026-03-27  
**Status:** Proposed (user-approved)  
**Owner:** ai_decision / risk_engine / execution / research

## 1. Objective

在当前交易系统中引入 `karpathy/autoresearch`，以“风险调整收益优先”为目标，建立可持续优化闭环。  
优化主窗口为滚动 90 天，且所有策略变更必须人工审批后才能上线。

## 2. Constraints (Confirmed)

1. Primary objective: 风险调整收益优先（Sharpe/Calmar 优先于绝对净利润）。
2. Evaluation window: 滚动 90 天。
3. Promotion policy: 全人工审批（禁止自动生效）。

## 3. Scope and Non-Goals

### In Scope

1. 使用 `autoresearch` 对“参数层”进行自动探索与候选生成。
2. 构建标准化评估器：90 天主评分 + 多场景稳定性评估。
3. 建立候选包产出、人工审批、受控发布流程。

### Non-Goals

1. 不改交易所适配器和下单协议。
2. 不开放资金权限给自动系统。
3. 不允许未经审批直接修改生产配置。
4. 不在第一阶段重写核心策略架构。

## 4. Integration Strategy (Recommended Approach 2)

采用“受约束的研究副驾驶”模式：

1. `autoresearch` 只探索可控参数，不直接执行交易。
2. 输出候选配置包与评估报告。
3. 人工审核通过后，才允许写入 profile 并切换激活版本。

这在收益潜力、实现复杂度、实盘风险之间提供了最佳平衡。

## 5. Search Space Design

仅开放以下参数族进入优化空间：

1. 触发阈值：`AI_SIGNAL_DELTA_THRESHOLD`
2. 仓位约束：`MAX_GROSS_SIDEWAYS`, `MAX_GROSS_TRENDING_*`, `MAX_NET`, `CAP_BTC_ETH`, `CAP_ALT`
3. 换手约束：`AI_TURNOVER_CAP`, `AI_TURNOVER_CAP_HIGH`
4. 执行节奏：`MAX_TRADES_PER_DAY`, `MIN_TRADE_INTERVAL_MIN`, `MIN_NOTIONAL_USD`
5. 防御开关和阈值：与执行质量、滑点、拒单相关的防御参数

禁止进入优化空间的内容：

1. 账户密钥、交易所路由、订单协议字段
2. 风控熔断硬规则的禁用或绕过
3. 审批流程本身

## 6. Data and Evaluation Pipeline

## 6.1 Inputs

1. `data/reporting.db`（审计、执行、状态）
2. Redis 历史导出（`alpha.target`, `risk.approved`, `exec.reports`, `md.features.*`）
3. 当前活动 profile 与历史 profile 快照

## 6.2 Evaluator

新增研究评估器（建议路径：`research/strategy_score.py`）：

1. 主分：90 天风险调整收益综合分（Sharpe + Calmar - 回撤惩罚 - 稳定性惩罚）
2. 约束惩罚：换手超限、拒单率恶化、滑点恶化、成交退化
3. 子集评估：按市场状态分桶（SIDEWAYS/TRENDING/VOLATILE）
4. 稳定性检验：滚动窗口方差与最差分位表现

## 6.3 Validation

1. Walk-forward（滚动训练/验证切分）
2. 时间切分防泄漏（严格先训练后验证）
3. 与当前基线 profile 对照评估（默认 `V9_recovery`）

## 7. Output and Governance

每次 `autoresearch` 运行输出一个“候选包”：

1. `candidate_profile.json`（参数）
2. `report.md`（核心指标、对比、失败区间）
3. `diff.md`（相对当前 profile 的参数差异）
4. `risk_notes.md`（潜在回撤与执行质量风险）

命名建议：`V9_ar_YYYYMMDD_runN`

审批门禁（必须全部满足）：

1. 90 天主分优于当前基线
2. 最大回撤不恶化（或在可接受阈值内）
3. 执行质量不过红线（reject/slippage/latency）
4. 人工审核通过

## 8. Runtime Promotion Flow

1. 研究任务生成候选包（离线）。
2. 人工评审候选包并记录审批结论。
3. 审批通过后，将候选参数写入 `config/trading_params.json` 新版本段。
4. 切换 `active_version` 到新候选。
5. 先小窗口观察，再决定保留或回滚。

回滚策略：

1. 一键回切上一个稳定 profile。
2. 保留候选包与事故上下文用于复盘。

## 9. Architecture Changes

新增模块（建议）：

1. `research/autoresearch_runner.py`：调度 `autoresearch` 实验
2. `research/strategy_score.py`：风险调整收益评分
3. `research/candidate_pack.py`：候选包生成与落盘
4. `research/promotion_gate.py`：审批门禁校验（只做检查，不自动上线）

修改模块：

1. `config/trading_params.json`：增加 AR 候选版本段
2. `docs/runbook.md`：增加“AR 候选审核与上线/回滚”步骤

## 10. Error Handling

1. 数据不足：候选运行直接失败并标记 `insufficient_data`。
2. 指标异常：评分器输出 `invalid_metrics` 并拒绝候选。
3. 评估器异常：候选包不产生上线建议。
4. 审批缺失：即使评分优秀也禁止发布。

## 11. Test Strategy

1. Unit
   - 评分器对已知样本的分数单调性
   - 约束惩罚触发正确性
   - 候选包结构完整性
2. Integration
   - 从历史数据生成候选并产出完整报告
   - 审批前后发布路径行为验证
3. Regression
   - 基线 profile 与候选 profile 的 90 天对照结果稳定

## 12. Success Criteria

以当前基线为参照，在人工审批前提下满足：

1. 90 天风险调整收益主分显著提升
2. 最大回撤不恶化到阈值外
3. 执行质量指标无系统性退化
4. 候选升级与回滚流程可重复、可审计

## 13. Rollout Plan (High-Level)

1. Phase 1: 建立离线评分与候选包产出，不做上线。
2. Phase 2: 建立审批门禁与 runbook 流程，允许手工小规模上线。
3. Phase 3: 周期化研究运营，持续优化候选质量与稳健性。

