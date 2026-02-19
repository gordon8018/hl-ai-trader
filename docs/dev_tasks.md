# Codex 开发任务拆解（可逐条验收）

## T1: Streams 基础设施
- [x] RedisStreams 封装：xadd_json / xreadgroup_json / xack / get_json / set_json
- [x] 每个 stream 自动创建 group（NOGROUP -> XGROUP CREATE）

验收：任意服务首次启动不报 NOGROUP，能读写 streams。

## T2: 数据模型
- [x] Pydantic schemas：FeatureSnapshot1m/StateSnapshot/TargetPortfolio/ApprovedTargetPortfolio/ExecutionPlan/OrderIntent/ExecutionReport
- [x] JSON 序列化与反序列化一致（已加 round-trip 测试与 schema_error audit）

验收：schemas round-trip 无丢字段，异常输入能抛错并 audit。

## T3: market_data MVP
- [x] allMids 拉 mid
- [x] 计算 ret_1m/5m/1h + vol_1h
- [x] 每分钟写 md.features.1m

验收：连续 60 分钟每分钟 1 条；缺失币种会写 audit.logs。

## T4: portfolio_state MVP
- [x] clearinghouseState/openOrders/allMids 对账
- [x] 写 state.snapshot + latest.state.snapshot

验收：latest.state.snapshot TTL 内持续更新，positions/open_orders 与交易所一致。

## T5: ai_decision baseline + LLM
- [x] baseline mom/vol 输出 alpha.target
- [ ] LLM 严格 JSON 输出（解析失败 fallback）
- [ ] 平滑 + turnover cap + confidence gating
- [ ] audit.logs 记录 used/raw_llm/gross/turnover（当前仅写 alpha.target）

验收：alpha.target 每分钟输出已达成；turnover/gross 约束与审计未完整。

## T6: risk_engine
- [x] per-symbol cap、gross/net cap、turnover cap
- [x] 输出 mode + rejections
- [x] 状态缺失 -> HALT

验收：risk.approved 每分钟输出，裁剪理由可解释。（REDUCE_ONLY 触发逻辑待补）

## T7: execution DRY_RUN
- [x] 接收 risk.approved -> exec.plan/orders/reports
- [x] 幂等 client_order_id + dedup
- [x] rate limiter（orders/min, cancels/min）

验收：DRY_RUN 下 exec.reports 持续 ACK，重复消息不重复执行。

## T8: execution live（主网）
- [ ] SDK 下单 + cancel（代码已具备，未验证）
- [ ] orderStatus 轮询直到终态/超时（代码已具备，未验证）
- [ ] 超时 cancel + report CANCELED（代码已具备，未验证）

验收：主网小单能 ACK 并最终写 FILLED/CANCELED。

## T9: DLQ 与重试
- [x] retry_count + max_retries（ai_decision / risk_engine / execution 统一策略）
- [x] 超限写 dlq.* + audit（统一 DLQ/RETRY 事件）

验收：人为制造错误可进入 DLQ。

## T10: Observability
- [x] Prometheus metrics：in/out/errors/latency（所有服务接入）
- [x] 每服务 /metrics 可抓（新增 error_streak 基础告警 gauge）

验收：Grafana/Prometheus 能看到各服务 QPS 与错误。
