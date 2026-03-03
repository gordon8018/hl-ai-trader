# 服务：reporting

## 1. 职责
- 消费 exec.reports / state.snapshot / audit.logs
- 落地到 SQLite，提供交易记录与盈亏曲线的数据源
- 供 Grafana/BI 查询使用

## 2. 输入
- Stream: exec.reports（ExecutionReport）
- Stream: state.snapshot（StateSnapshot）
- Stream: audit.logs（审计事件）

## 3. 输出
- SQLite DB：默认 `data/reporting.db`

## 4. 表结构（核心）
- exec_reports：订单/成交回报
- state_snapshots：账户净值与持仓快照
- audit_events：审计事件

## 5. 环境变量
- REDIS_URL（必填）
- REPORT_DB_PATH（默认 data/reporting.db）
- REPORT_POLL_MS（默认 1000）

## 6. Grafana 使用
- 通过 SQLite 数据源插件连接 `data/reporting.db`
- 示例指标：
  - equity_usd 随时间曲线
  - 单币成交/拒单统计
  - 日内 PnL = equity_usd - 当日首个 equity_usd
