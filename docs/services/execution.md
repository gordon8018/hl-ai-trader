# 服务：execution（执行引擎）

## 1. 职责
- 将 ApprovedTargetPortfolio 转换为执行计划（TWAP）
- 下单/撤单/（可选）改价
- 管理幂等、限速、超时、状态跟踪
- 输出 exec.plan / exec.orders / exec.reports

## 2. 输入
- Stream: risk.approved
- Redis key: latest.state.snapshot
- Stream: ctl.commands（可选，建议实现）
- 交易所：/exchange 下单、/info orderStatus 跟踪

## 3. 输出
- Stream: exec.plan（ExecutionPlan）
- Stream: exec.orders（OrderIntent）
- Stream: exec.reports（ExecutionReport）
- audit.logs（exec.done、rate_limited、submit_error、cancel_error）
- Redis: dedup:{client_order_id}

## 4. 执行策略（默认 TWAP）
- qty = (Δw * equity) / mid
- close-first：先减仓再加仓
- 分片：TWAP_SLICES
- 价格护栏：px_guard_bps
- 超时：SLICE_TIMEOUT_S
- 超时后：CANCEL；（可选）一次 REPLACE（更靠近 mid）

## 5. 幂等
- client_order_id = cycle_id:symbol:slice_idx:side
- dedup key 存 status+exchange_order_id
- 若 dedup.status>=ACK，严禁重复 PLACE

## 6. 限速
- Redis token bucket：
  - orders/min
  - cancels/min
- 限速触发：跳过该 slice 并 audit

## 7. 状态跟踪
- 下单 ACK 后，对 oid 轮询 orderStatus 直到：
  - FILLED → report FILLED
  - 超时 → cancel → report CANCELED
- portfolio_state 也会对 oid 做统一跟踪并写 state.events（双保险）

## 8. 质量/验收
- DRY_RUN=true 时：不下单，但仍产出 plan/orders/reports
- DRY_RUN=false 时：能下真实小单，报告 ACK 与后续状态
- 任何异常必须写 audit.logs
