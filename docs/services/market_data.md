# 服务：market_data

## 1. 职责
- 获取行情数据
- 聚合分钟级 bar/特征
- 每分钟生成并写入 `md.features.1m`（系统心跳）

## 2. 输入
- 无（直接连交易所）

## 3. 输出
- Stream: `md.features.1m` -> FeatureSnapshot1m

## 4. MVP 实现（推荐）
- 每 2s 调用 `POST /info {type: allMids}` 得到各币 mid
- 维护每个币 mid 的滚动队列（覆盖至少 90 分钟）
- 每分钟切换时计算：
  - ret_1m/5m/1h
  - vol_1h（用 1m 对齐采样的 log return 方差）
- 写入 `md.features.1m`，`env.cycle_id` 使用 asof_minute 转换

## 5. 增强实现（后续）
- 使用 WS 订阅 `l2Book`（订单簿快照/增量），计算：
  - spread_bps
  - book_imbalance
  - liquidity_score（基于深度/价差）
- WS 断线重连：重订阅 + 清空本地 book + 写 state.events/audit.logs

## 6. 质量/验收
- md.features.1m 每分钟稳定产出 1 条
- 缺失币种必须在 audit.logs 记录（避免 silent failure）
- 发生 HTTP/WS 异常必须写 audit.logs
