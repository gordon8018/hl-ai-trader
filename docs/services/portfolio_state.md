# 服务：portfolio_state

## 1. 职责
- 维护账户/仓位/挂单的本地权威镜像
- 周期对账（最终以交易所为准）
- 跟踪订单状态（orderStatus），输出 state.events

## 2. 输入
- Stream: `exec.reports`（用于收集 exchange_order_id，辅助快速跟踪）
- 交易所：/info 接口（clearinghouseState/openOrders/allMids/orderStatus）

## 3. 输出
- Stream: `state.snapshot` -> StateSnapshot
- Redis key: `latest.state.snapshot`（TTL=60s）
- Stream: `state.events`（orderStatus、对账差异、异常）
- audit.logs（错误、对账汇总）

## 4. 对账频率
- 默认每 10s：
  - clearinghouseState（仓位/账户价值）
  - openOrders（挂单）
  - allMids（mark/mid）
- 将结果规范化为 StateSnapshot

## 5. 订单状态跟踪
- 维护 Redis set：`active.exchange.oids`
- 消费 exec.reports：若带 exchange_order_id，加入 set
- 每 5s 对 set 中 oid 调用 orderStatus：
  - 将结果写 state.events（event="orderStatus"）
  - 识别终态（filled/canceled/rejected）后从 set 移除

## 6. 失败与健康
- 连续对账失败 > 30s：latest.state.snapshot.health.reconcile_ok=false
- 写 audit.logs，建议触发 ctl HALT（可选自动化）

## 7. 质量/验收
- latest.state.snapshot 必须持续更新
- open_orders 与交易所一致
- 对账失败可恢复（网络恢复后自动继续）
