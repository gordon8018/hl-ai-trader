# Trading Non-Execution Stability Design

**Date:** 2026-03-27
**Status:** Proposed (user-approved in chat)
**Owner:** ai_decision / execution / ops

## 1. Problem Statement

当前系统“持续不交易”的现象不是单点故障，而是三类问题叠加：

1. 决策层长期输出 `HOLD`，且目标仓位持续为全 0。
2. 目标仓位与实际持仓存在脱钩时，重平衡判定仍可能返回 `HOLD`。
3. 执行层控制流 (`ctl.commands`) 存在坏消息循环重试，导致 `ctl.error` 事件风暴并污染观测信号。

本设计目标是恢复系统“应交易时可交易、应平仓时可平仓”的基本能力，并降低排障成本。

## 2. Goals and Non-Goals

### Goals

1. 修复重平衡判定基准，避免“有持仓却不平仓”。
2. 修复 `ctl.commands` 的格式兼容与 ACK 行为，消除无限重试。
3. 增加仓位一致性保护与审计事件，提升可解释性。
4. 建立参数分档与验收指标，支持低风险恢复。

### Non-Goals

1. 不重写双层策略架构（Layer1/Layer2 保持不变）。
2. 不在本轮引入新交易策略因子。
3. 不调整交易所适配器核心执行逻辑（只改控制流健壮性）。

## 3. Current-State Findings (from logs + DB + redis)

1. 24h 内 `ai_decision` 侧 `ai.hold_decision` 近乎全量，主因为 `signal_delta_below_threshold`。
2. `risk_engine` 下游输入几乎全为 `HOLD`，执行侧对应 `exec.skip_decision_action`。
3. Redis 中 `latest.alpha.target` 为全零，但 `latest.state.snapshot` 仍有实际仓位，说明存在“目标/实际脱钩”。
4. `execution` 的 `ctl.error` 高发来自 `ctl.commands` 待处理消息反复投递；消息体常见包装形式为 `{"p":"<json>"}`，与当前解析预期不一致。

## 4. Design Overview

采用“稳定化修复”方案（推荐）：

1. **Decision Fix:** 将重平衡判定基准由 `prev_w` 切换为 `current_w`。
2. **Control-Stream Fix:** 为 `ctl.commands` 增加包装格式解包与异常 ACK 防重试机制。
3. **Consistency Guard:** 新增仓位一致性保护，确保目标/实际偏离时优先执行风险收敛动作。
4. **Observability:** 增加关键指标与审计字段，支持快速定位“为何不交易”。
5. **Progressive Rollout:** 通过参数分档进行分阶段恢复。

## 5. Detailed Design

### 5.1 ai_decision: Rebalance Baseline Correction

#### Current behavior

`should_rebalance(...)` 使用 `candidate_w` 与 `prev_w` 的差值作为信号强度。
当 `prev_w` 已经是全零而实际持仓 `current_w` 非零时，`delta` 可为 0，从而继续 `HOLD`。

#### New behavior

1. `signal_delta` 改为基于 `candidate_w` 与 `current_w` 计算。
2. 审计事件中同时保留：
   - `delta_vs_current`
   - `delta_vs_prev`
3. `decision_reason` 细化：
   - `signal_delta_below_threshold_vs_current`
   - `signal_delta_below_threshold_vs_prev`（仅观测）

#### Expected effect

当真实仓位存在但目标趋零时，系统会触发平仓型 `REBALANCE`，不再因历史目标值卡住。

### 5.2 ai_decision: Position/Target Consistency Guard

新增轻量保护逻辑：

1. 若 `sum(abs(current_w)) > epsilon` 且 `sum(abs(prev_w)) <= epsilon`，标记 `position_target_mismatch=true`。
2. 若此时 `candidate_w` 仍为全零，则允许继续走 rebalance（平仓/对齐动作），不受“与 prev_w 相同”影响。
3. 发布审计事件 `ai.position_target_mismatch`，附带：
   - `current_gross`
   - `prev_target_gross`
   - `candidate_gross`
   - `asof_minute`

`epsilon` 默认建议 `0.002`（20 bps gross），通过配置可调。

### 5.3 execution: ctl.commands Robust Parsing + Ack Policy

#### Current behavior

1. 解析逻辑假定消息结构直接包含 `env/data`。
2. 对异常消息写 `ctl.error` 但不 `xack`，导致 pending 消息被反复读取。

#### New behavior

1. 增加 message-normalizer：
   - 若 payload 含 `p` 字段，尝试 JSON 反序列化后再走 `require_env`。
   - 同时兼容当前原生 `env/data` 格式。
2. 错误分类：
   - `ctl.invalid_format`（不可解析）
   - `ctl.invalid_command`（命令非法）
   - `ctl.apply_error`（应用失败）
3. ACK 策略：
   - 对不可恢复错误（格式错、命令非法）必须 `xack`，必要时投递 `dlq.ctl.commands`。
   - 对可恢复错误（短暂 Redis/写失败）允许重试，但要限次并可转 DLQ。

#### Expected effect

清除无限重试，`ctl.error` 事件量恢复到可观测范围，降低噪声与资源消耗。

### 5.4 Parameter Profiles (safe/recovery/prod)

在 `trading_params.json` 增加可切换运行档位（沿用 version 机制）：

1. `V9_safe`：最保守，仅验证修复正确性。
2. `V9_recovery`：恢复交易能力，适度放宽触发门槛。
3. `V9_prod`：现网目标档位。

建议仅在代码修复稳定后再切到 `V9_recovery`。

### 5.5 Observability Additions

新增/增强指标与审计字段：

1. `ai_target_actual_gap_gross`
2. `ai_rebalance_delta_vs_current`
3. `execution_ctl_pending_count`
4. `execution_ctl_times_delivered_p95`
5. `decision_reason` 枚举计数（Prometheus label 或审计聚合）

## 6. Data Flow Changes

### Before

`candidate_w` -> compare with `prev_w` -> hold/rebalance

### After

`candidate_w` -> compare with `current_w` -> hold/rebalance

并行执行：

`ctl.commands` -> normalize (`p` unwrap) -> validate -> apply/ack/dlq

## 7. Error Handling Strategy

1. **Fail closed on parsing:** 无法解析的 `ctl` 消息不应用，但必须 ACK + 审计。
2. **Fail observable:** 对每种拒绝路径写统一结构化事件（含 `reason_code`）。
3. **Bounded retries:** 可恢复错误限定最大重试次数，防止无限 pending。

## 8. Test Plan

### Unit tests

1. `ai_decision`
   - `candidate=0, prev=0, current!=0` 场景应能触发对齐型 rebalance。
   - `delta_vs_current` 与 `decision_reason` 正确。
   - `position_target_mismatch` 审计事件正确发出。

2. `execution`
   - 兼容 `{"p":"..."}` 与原生 `env/data` 两种格式。
   - 不可恢复错误会 ACK，不再重复投递。
   - 命令非法走 `ctl.invalid_command`。

### Integration checks

1. 回放最近 24h 数据：确认 `ctl.error` 数量显著下降。
2. 构造“实际有仓位、目标全零”状态：确认出现平仓相关执行事件。

## 9. Rollout Plan

1. 阶段 1：发布代码修复（不改参数），观察 4-8 小时。
2. 阶段 2：切 `V9_recovery`，观察 24 小时。
3. 阶段 3：评估后回归 `V9_prod` 或保留恢复档。

回滚：

1. 配置回滚到旧版本。
2. 代码回滚到修复前 tag。
3. 重启服务并验证核心健康指标。

## 10. Risks and Mitigations

1. **风险：** 修复后交易频率短时上升。
   - **缓解：** 保留 `MAX_TRADES_PER_DAY` 与 `MIN_TRADE_INTERVAL_MIN`，先不放开。
2. **风险：** ctl 消息格式存在更多历史变体。
   - **缓解：** normalize 采用“显式检测 + 严格审计 + ACK”策略。
3. **风险：** 指标过多导致观测复杂。
   - **缓解：** 首批只上 4 个核心指标。

## 11. Acceptance Criteria

1. 24h 内 `execution:ctl.error` 降低至少 95%。
2. `ctl.commands` pending 深度不再长期堆积，`times_delivered` 不再持续增长。
3. 当存在实际仓位且目标要求归零时，系统在 1 个决策窗口内触发 rebalance。
4. 审计中 `decision_reason` 不再单一被 `signal_delta_below_threshold` 长期垄断。

