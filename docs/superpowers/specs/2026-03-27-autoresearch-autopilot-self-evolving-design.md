# Autoresearch Autopilot Self-Evolving Trading Design

**Date:** 2026-03-27  
**Status:** Proposed (user-approved in chat)  
**Owner:** research / ai_decision / risk_engine / execution / ops

## 1. Objective

将当前系统的 `autoresearch` 与实盘执行融合为“可自动进化”的闭环系统，在中等风险偏好下实现：

1. 候选策略自动生成
2. 影子验证 24h
3. 自动小仓上线（canary）
4. 不达标自动回滚
5. 可随时查询“当前线上版本”

## 2. Confirmed Constraints

1. Risk appetite: 中等风险（收益与稳定性平衡）
2. Promotion policy: 影子运行 24h 后自动小仓上线
3. Observability requirement: 用户可实时知道当前线上版本
4. Version visibility medium: Redis + CLI

## 3. Scope and Non-Goals

### In Scope

1. 引入自动部署状态机（candidate/shadow/canary/stable/rollback）
2. 自动门禁评估与自动晋级/回滚
3. Redis 版本可见性协议与 CLI 查询脚本
4. Runbook 更新（自动模式、回滚、紧急停机）

### Non-Goals

1. 不改交易所下单协议
2. 不绕过风险熔断
3. 不在第一阶段支持“直接全量自动上线”

## 4. Architecture Overview

采用“双轨 + 状态机”：

1. **Research Track:** `autoresearch` 生成候选参数包
2. **Shadow Track:** 候选版本在影子环境评估 24h（不影响主实盘）
3. **Live Track:** 仅当门禁通过，自动切到 canary（小仓）；达标后升 stable

核心治理：

1. 每次状态迁移都写审计事件
2. 每次生效版本都写 Redis `deployment.current_version`
3. 触发红线自动回滚到最近 stable

## 5. Deployment State Machine

状态：

1. `research_candidate`
2. `shadow_running`
3. `canary_live`
4. `stable`
5. `rollback`

迁移规则：

1. `research_candidate -> shadow_running`：候选包生成完成
2. `shadow_running -> canary_live`：影子 24h 门禁通过
3. `canary_live -> stable`：canary 观察窗达标
4. `shadow_running/canary_live -> rollback`：任一红线触发
5. `rollback -> stable`：回切至最近 stable 后确认健康

## 6. Auto Gate Policy

### 6.1 Shadow Gate (24h)

必须全部满足：

1. 风险调整主分优于基线（90d 主目标 + 短窗一致性）
2. 最大回撤不恶化超过阈值
3. 执行质量不过红线（reject/slippage/latency）
4. 样本充分（防止数据不足误判）

### 6.2 Canary Gate

必须全部满足：

1. 小仓期收益/回撤符合阈值
2. 执行稳定（无异常拒单风暴/时延劣化）
3. 风控事件无升级（无持续熔断）

## 7. Auto Rollback Policy

触发任一即回滚：

1. drawdown 超阈值
2. reject rate 超阈值
3. slippage 超阈值
4. 延迟异常持续
5. 审计事件出现关键错误模式（连续失败）

回滚动作：

1. 将 `active_version` 切回最近 stable
2. 写 `deployment.current_version`（stage=`rollback` + previous_version）
3. 重启必要服务（ai_decision/risk_engine/execution）
4. 写入审计事件 `deployment.rollback_triggered`

## 8. Version Visibility (Redis + CLI)

Redis key: `deployment.current_version`

建议 JSON 结构：

1. `version`
2. `stage` (`shadow_running|canary_live|stable|rollback`)
3. `promoted_at`
4. `previous_version`
5. `reason`
6. `run_id`

CLI 查询：

1. `redis-cli GET deployment.current_version`
2. 新增脚本 `scripts/show_current_version.py`（结构化输出）

## 9. Proposed Implementation Units

新增：

1. `research/deployment_state.py`：状态机与迁移校验
2. `research/promotion_orchestrator.py`：影子评估、自动晋级、自动回滚
3. `scripts/show_current_version.py`：当前版本查询

修改：

1. `config/trading_params.json`：新增自动模式相关阈值/开关
2. `docs/runbook.md`：自动进化流程与紧急处理

## 10. Safety Controls

1. 默认保守：自动模式开关默认关闭
2. 限仓：canary 阶段硬限制仓位与频率
3. 幂等：同一 run_id 只允许一次状态迁移
4. 审计：所有迁移、拒绝、回滚必须可追溯

## 11. Test Strategy

1. Unit
   - 状态机合法/非法迁移
   - 门禁阈值判定
   - 回滚触发逻辑
2. Integration
   - candidate -> shadow -> canary -> stable 全链路
   - 红线触发 -> rollback 链路
3. Runtime checks
   - `deployment.current_version` 正确更新
   - CLI 查询输出与 Redis 一致

## 12. Success Criteria

1. 自动闭环可运行且可回滚
2. 版本可见性稳定可靠（Redis/CLI）
3. 相比人工流程，迭代周期显著缩短
4. 风险指标未明显劣化（回撤、执行质量）

## 13. Rollout Plan

1. Phase 1: 接入状态机与版本可见性（不自动晋级）
2. Phase 2: 启用 shadow 自动评估与 gate 判断（仍人工确认）
3. Phase 3: 启用 canary 自动晋级与自动回滚
4. Phase 4: 稳定后再评估是否扩大自动化范围

