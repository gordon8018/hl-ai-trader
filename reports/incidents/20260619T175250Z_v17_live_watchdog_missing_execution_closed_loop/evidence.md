# V17 live watchdog missing execution closed-loop incident

timestamp_utc: 20260619T175250Z
branch: fix/v17-execution-ctl-mode-regression-test
sha: c1ecc0730953b8ac46b4e7a98cb2d08097db24d1

## Watchdog stdout
🚨 hl-ai-trader live watchdog 异常
- missing process: services.execution.app
- 订单流: {"alpha.target": 0, "dlq.alpha.target": 0, "dlq.risk.approved.v17_live": 0, "exec.orders": 0, "exec.reports.v17_live": 31, "risk.approved.v17_live": 4300}
- 进程: {"services.execution.app": false, "services.market_data.app": true, "services.risk_engine.app": true}

## Process evidence
```
91251
65109
execution.pid=59634
  PID  PPID COMMAND
```

## Redis evidence before HALT
```
latest.state.snapshot type=string
latest.state.snapshot strlen=980
ctl.mode type=string
ctl.mode strlen=4
alpha.target type=stream
alpha.target xlen=0
risk.approved.v17_live type=stream
risk.approved.v17_live xlen=4302
exec.orders type=none
exec.reports.v17_live type=stream
exec.reports.v17_live xlen=31
dlq.risk.approved.v17_live type=none
dlq.alpha.target type=none
latest risk approved:
1781891543964-0
p
{"env":{"event_id":"66457133-7cb1-41a0-9c33-f25259a3e9b4","ts":"2026-06-19T17:52:23Z","source":"risk_engine","cycle_id":"20260619T1751Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:51:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010279923043016446,"peak_equity_usd":1386.911962,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.7542636816823861,"basis_abs_avg":0.0,"oi_change_abs_max":0.005295155030895504}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":63118.5,"mark_px_ETH":1703.25,"mark_px_SOL":69.2605,"mark_px_ADA":0.16153,"mark_px_DOGE":0.083234}}}
latest exec reports:
1781885025058-0
p
{"env":{"event_id":"53eac243-f133-44c8-892f-2fa8fb9ab259","ts":"2026-06-19T16:03:45Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884989561-0
p
{"env":{"event_id":"5ee4f6d0-d1d5-4d2a-89a2-e2e508d5fcd2","ts":"2026-06-19T16:03:09Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884984236-0
p
{"env":{"event_id":"c15fb55d-6538-4344-8614-bad9edcae60b","ts":"2026-06-19T16:03:04Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
exec consumer groups:
ERR no such key

```

## HALT action 20260619T175305Z
```
sent ctl.commands id=1781891586077-0 cmd=HALT reason=watchdog incident 20260619T175305Z
ctl.mode type/value:
string
HALT
waiting for fresh risk approval...
1781891586302-0
p
{"env":{"event_id":"bc83fd8a-bdd4-4556-b3ce-078879ae8bbf","ts":"2026-06-19T17:53:06Z","source":"risk_engine","cycle_id":"20260619T1752Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:52:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[{"symbol":"*","reason":"live_target_real_money_not_allowed","original_weight":0.0,"approved_weight":0.0}],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010268857027541815,"peak_equity_usd":1386.927469,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.7328034651008283,"basis_abs_avg":0.0,"oi_change_abs_max":0.005305476911290574}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":63115.5,"mark_px_ETH":1703.05,"mark_px_SOL":69.2455,"mark_px_ADA":0.1615,"mark_px_DOGE":0.083217}}}
1781891581871-0
p
{"env":{"event_id":"87405edd-0152-4239-9715-193e45914504","ts":"2026-06-19T17:53:01Z","source":"risk_engine","cycle_id":"20260619T1751Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:51:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010268857027541815,"peak_equity_usd":1386.927469,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.7542636816823861,"basis_abs_avg":0.0,"oi_change_abs_max":0.005295155030895504}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":63115.5,"mark_px_ETH":1703.05,"mark_px_SOL":69.2455,"mark_px_ADA":0.1615,"mark_px_DOGE":0.083217}}}
```
