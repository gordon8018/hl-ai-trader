# Redis Streams 事件总线规范

本系统使用 Redis Streams 作为事件总线，要求**可追溯、可重放、可恢复**。所有消息必须包含统一 Envelope（env），payload 统一为 JSON 串存储在单字段 `p` 中（避免 Redis field 数量限制）。

## 1. 统一 Envelope（env）

每条消息的 `env` 必须包含：

- `event_id`: UUID
- `ts`: UTC ISO8601（秒精度）
- `source`: 服务名（market_data / portfolio_state / ai_decision / risk_engine / execution / web_ui）
- `cycle_id`: 分钟周期 ID，例如 `20260218T1600Z`（用于追踪同一决策周期）
- `schema_version`: 字符串（固定 "1.0"）
- `retry_count`: 重试次数（整数，默认 0）

消息结构统一为：

```json
{
  "env": { ... },
  "data": { ... },
  "event": "optional_event_name"
}
```

## 2. Streams 列表与用途

| Stream         | Producer        | Consumer Group    | Consumers       | 用途                               |
| -------------- | --------------- | ----------------- | --------------- | -------------------------------- |
| md.features.1m | market_data     | ai_grp            | ai_decision     | 每分钟特征快照（系统心跳触发器）                 |
| state.snapshot | portfolio_state | (optional)        | web_ui/monitor  | 账户状态镜像快照                         |
| state.events   | portfolio_state | (optional)        | web_ui/monitor  | 订单状态事件、对账差异、异常                   |
| alpha.target   | ai_decision     | risk_grp          | risk_engine     | AI 输出目标组合                        |
| risk.approved  | risk_engine     | exec_grp          | execution       | 风控裁剪后的目标组合                       |
| exec.plan      | execution       | (optional)        | web_ui/monitor  | 执行计划（TWAP 分片）                    |
| exec.orders    | execution       | (optional)        | web_ui/monitor  | 下单/撤单/改价意图                       |
| exec.reports   | execution       | state_reports_grp | portfolio_state | 执行回报（ACK/FILL/REJECT/CANCEL）     |
| ctl.commands   | human/web_ui    | ctl_grp           | all services    | 控制命令（HALT/RESUME/REDUCE_ONLY/参数） |
| audit.logs     | all             | (optional)        | web_ui/monitor  | 审计日志（必写）                         |

说明：MVP 阶段可不实现 web_ui，但 audit.logs 必须写，便于排错与复盘。

## 3. 消费组规则（XREADGROUP）

- 所有消费者必须使用 XREADGROUP 读取 `>` 新消息。
- 处理成功必须 XACK。
- 处理失败不得 XACK，进入 PEL（pending entries list），由同 consumer 重试或用 XCLAIM 转移。
- 每个服务应有一个“pending 清理线程”（可选，MVP 后做）。

## 4. DLQ（死信队列）

- 每条消息通过 env.retry_count 控制重试。超过 MAX_RETRIES 后写入 DLQ：
  - dlq.alpha.target
  - dlq.risk.approved
  - dlq.exec.orders
- 并写入 audit.logs：
  - 原始 payload（可截断）
  - 失败原因
  - 重试次数

## 5. 幂等性要求

- 执行层必须用 client_order_id 幂等：

  ```text
  client_order_id = cycle_id:symbol:slice_idx:side
  ```

- Redis key：dedup:{client_order_id} 记录 {status, exchange_order_id, ts}（TTL 建议 1h）。
- 如果 dedup 已存在且状态 >= ACK，严禁重复下单。

## 6. 重放（Replay）

- 重放策略建议：
  - 从 md.features.1m 起重放，按时间顺序把消息重新写入到临时 stream（如 replay.md.features.1m），让 ai_decision/risk/execution 走同一链路（execution 可强制 DRY_RUN）。
- replay 必须隔离到独立的 Redis DB 或 prefix，避免污染线上。
