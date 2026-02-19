# Runbook：主网小资金灰度操作手册

## 0. 强制原则
- 第一次启动必须 DRY_RUN=true
- 任何不确定状态（对账失败/断线/异常频繁）先 HALT
- 风控阈值保持保守（MAX_GROSS<=0.40，BTC/ETH cap<=0.15，ALT cap<=0.10）

## 1. 部署前检查清单
- Redis 可用（AOF 开启）
- 服务启动顺序：redis -> market_data & portfolio_state -> ai_decision -> risk_engine -> execution
- .env 已配置：
  - UNIVERSE=BTC,ETH,SOL,ADA,DOGE
  - HL_HTTP_URL=https://api.hyperliquid.xyz
  - DRY_RUN=true
- 先不填私钥（避免误触发）

## 2. DRY_RUN 验证（至少 30 分钟）
观察 Redis Streams（或日志）：
- md.features.1m 每分钟 1 条
- alpha.target 每分钟 1 条（used=baseline 或 llm）
- risk.approved 每分钟 1 条（mode=NORMAL）
- exec.plan / exec.orders / exec.reports 持续产生（DRY_RUN ACK）

必查 audit.logs：
- error 数量应接近 0
- turnover/gross 是否合理
- 任何缺失币种/状态缺失必须修复后再上主网

## 3. 开启主网小资金实盘
- 填入 HL_ACCOUNT_ADDRESS / HL_PRIVATE_KEY
- DRY_RUN=false
- 建议先只启用 BTC/ETH（或将 ALT cap 临时降到 0.02）
- 执行层观察：
  - ACK 后是否能在 orderStatus / state.events 看到终态
  - 对账 equity/positions 是否一致

## 4. 一键熔断（HALT）
触发条件建议（任一满足）：
- portfolio_state 连续对账失败 > 30s
- execution submit_error/cancel_error 频繁
- rate_limited 持续触发
- 策略输出 turnover 连续超限
- equity 回撤达到阈值（DD>=3%）

操作：
- 写入 ctl.commands: {"cmd":"HALT","reason":"..."}
- 验证 execution 不再下新单
- 若需要：手动平仓（可另写 reduce_all 工具）

## 5. 恢复（RESUME）
- 先确认：
  - latest.state.snapshot.reconcile_ok=true
  - WS/HTTP 恢复稳定
- 写 ctl.commands: {"cmd":"RESUME"}
- 建议先 REDUCE_ONLY 运行 5~10 分钟，再切 NORMAL

## 6. 常见故障排查
### 6.1 md.features.1m 不出
- 检查 market_data 是否能请求 allMids（网络/DNS）
- audit.logs 是否有 HTTP 错误
- Redis 是否写入失败

### 6.2 risk.approved 一直 HALT
- latest.state.snapshot 是否缺失/过期
- portfolio_state 是否对账失败
- 是否 ctl.commands 处于 HALT

### 6.3 execution 下单后无后续状态
- orderStatus 轮询是否正常
- portfolio_state 是否把 oid 加入 active set 并持续写 state.events
- 可能是 SDK 调用失败/签名失败：检查 exec.reports raw/error

### 6.4 频繁换手
- 提高 AI_SMOOTH_ALPHA（更平滑）
- 降低 TURNOVER_CAP
- 提高 AI_MIN_CONFIDENCE，低置信度时强制降低 gross

## 7. 回滚策略
- 发现 AI 不稳定：LLM_ENABLED=false，仅跑 baseline
- 发现执行异常：DRY_RUN=true 或直接 HALT
