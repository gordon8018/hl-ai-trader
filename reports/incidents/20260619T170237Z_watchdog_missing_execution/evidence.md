# V17 live watchdog incident evidence

timestamp_utc: 20260619T170237Z
branch: fix/v17-execution-ctl-mode-regression-test
git_sha: 337d04d8b6a2280217b93b7b79d8d9240d4787f2

## watchdog_output
🚨 hl-ai-trader live watchdog 异常
- missing process: services.execution.app
- 订单流: {"alpha.target": 0, "dlq.alpha.target": 0, "dlq.risk.approved.v17_live": 0, "exec.orders": 0, "exec.reports.v17_live": 31, "risk.approved.v17_live": 3998}
- 进程: {"services.execution.app": false, "services.market_data.app": true, "services.risk_engine.app": true}

## safety_action
sent ctl.commands id=1781888557143-0 cmd=HALT reason=watchdog incident 20260619T170237Z

## ctl_mode_after
HALT

## process_snapshot
  PID  PPID COMMAND
39438 48366 /opt/homebrew/bin/bash -lic set +m; set -a && source infra/.env && set +a && export REDIS_URL=redis://127.0.0.1:6381/0 V17_ALPHA_TARGET_STREAM=alpha.target.v17_live V17_ALPHA_LIVE_TARGET_APPROVED=true V17_ALPHA_REAL_MONEY_EXECUTION_ALLOWED=true V17_LIVE_EXECUTION=true V17_LIVE_REAL_MONEY_APPROVED=true V17_LIVE_STATE_MAX_AGE_SECONDS=120 && .venv/bin/python -m services.v17_alpha.app
39440 39438 .venv/bin/python -m services.v17_alpha.app
65109 48366 /Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m services.market_data.app
69950 48366 .venv/bin/python -m services.portfolio_state.app
91251 48366 .venv/bin/python -m services.risk_engine.app

## redis_types_and_lengths
ctl.mode TYPE=string
latest.state.snapshot TYPE=string
alpha.target TYPE=stream XLEN=0
risk.approved.v17_live TYPE=stream XLEN=4002
exec.orders TYPE=none
exec.reports.v17_live TYPE=stream XLEN=31
dlq.risk.approved.v17_live TYPE=none
dlq.alpha.target TYPE=none

## exec_orders_groups
ERR no such key


## latest_risk_5
1781888537562-0
p
{"env":{"event_id":"2d3027e2-ec6c-40af-a405-b99d745b1c0f","ts":"2026-06-19T17:02:17Z","source":"risk_engine","cycle_id":"20260619T1701Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:01:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010665240460152499,"peak_equity_usd":1386.37201,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.6990535328506269,"basis_abs_avg":0.0,"oi_change_abs_max":0.0036540067093302486}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":62934.5,"mark_px_ETH":1696.65,"mark_px_SOL":68.7075,"mark_px_ADA":0.160375,"mark_px_DOGE":0.082902}}}
1781888531841-0
p
{"env":{"event_id":"36dd8655-b7e1-4731-9c60-8e67c264144b","ts":"2026-06-19T17:02:11Z","source":"risk_engine","cycle_id":"20260619T1701Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:01:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010665240460152499,"peak_equity_usd":1386.37201,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.6990535328506269,"basis_abs_avg":0.0,"oi_change_abs_max":0.0036540067093302486}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":62934.5,"mark_px_ETH":1696.65,"mark_px_SOL":68.7075,"mark_px_ADA":0.160375,"mark_px_DOGE":0.082902}}}
1781888527466-0
p
{"env":{"event_id":"8dc3dc71-3318-4f1f-ac7a-6b74fd89fb66","ts":"2026-06-19T17:02:07Z","source":"risk_engine","cycle_id":"20260619T1701Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:01:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010676878080605148,"peak_equity_usd":1386.355702,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.6990535328506269,"basis_abs_avg":0.0,"oi_change_abs_max":0.0036540067093302486}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":62929.5,"mark_px_ETH":1696.55,"mark_px_SOL":68.7075,"mark_px_ADA":0.16037,"mark_px_DOGE":0.082902}}}
1781888519663-0
p
{"env":{"event_id":"3a4fa566-d1d9-4b74-8f5e-1a95c1187e79","ts":"2026-06-19T17:01:59Z","source":"risk_engine","cycle_id":"20260619T1700Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:00:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[{"symbol":"*","reason":"live_target_real_money_not_allowed","original_weight":0.0,"approved_weight":0.0}],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010676878080605148,"peak_equity_usd":1386.355702,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.7333199829652807,"basis_abs_avg":0.0,"oi_change_abs_max":0.0014685195643486804}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":62929.5,"mark_px_ETH":1696.55,"mark_px_SOL":68.7075,"mark_px_ADA":0.16037,"mark_px_DOGE":0.082902}}}
1781888515153-0
p
{"env":{"event_id":"0e3595a3-c588-451e-909e-e9e40d319eca","ts":"2026-06-19T17:01:55Z","source":"risk_engine","cycle_id":"20260619T1700Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T17:00:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010699941378091738,"peak_equity_usd":1386.323383,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.7333199829652807,"basis_abs_avg":0.0,"oi_change_abs_max":0.0014685195643486804}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":62918.5,"mark_px_ETH":1696.35,"mark_px_SOL":68.682,"mark_px_ADA":0.160365,"mark_px_DOGE":0.082905}}}

## latest_exec_reports_10
1781885025058-0
p
{"env":{"event_id":"53eac243-f133-44c8-892f-2fa8fb9ab259","ts":"2026-06-19T16:03:45Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884989561-0
p
{"env":{"event_id":"5ee4f6d0-d1d5-4d2a-89a2-e2e508d5fcd2","ts":"2026-06-19T16:03:09Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884984236-0
p
{"env":{"event_id":"c15fb55d-6538-4344-8614-bad9edcae60b","ts":"2026-06-19T16:03:04Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884978987-0
p
{"env":{"event_id":"ddebb420-9569-4696-bf4c-d48087a7777d","ts":"2026-06-19T16:02:58Z","source":"execution","cycle_id":"20260619T1601Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1601Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884975354-0
p
{"env":{"event_id":"583f30d6-27d5-44e5-8fc2-8bbd7513e92e","ts":"2026-06-19T16:02:55Z","source":"execution","cycle_id":"20260619T1601Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1601Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884975353-0
p
{"env":{"event_id":"0dfd9e9a-8ac0-4378-87ee-56b26ce3965b","ts":"2026-06-19T16:02:55Z","source":"execution","cycle_id":"20260619T1601Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1601Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884975351-0
p
{"env":{"event_id":"bc9e3e51-06bb-456e-a448-71efe5bdea0a","ts":"2026-06-19T16:02:55Z","source":"execution","cycle_id":"20260619T1601Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1601Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884975308-0
p
{"env":{"event_id":"b07e4558-fdf9-4218-88bc-3cdccd0520cd","ts":"2026-06-19T16:02:55Z","source":"execution","cycle_id":"20260619T1601Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1601Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884975306-0
p
{"env":{"event_id":"0d820706-00b8-4256-a6fd-5fea9bc2bce3","ts":"2026-06-19T16:02:55Z","source":"execution","cycle_id":"20260619T1600Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1600Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884975305-0
p
{"env":{"event_id":"023735a1-418b-4162-8731-b12cea13e7d2","ts":"2026-06-19T16:02:55Z","source":"execution","cycle_id":"20260619T1600Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1600Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}

## latest_snapshot_head
{"env": {"event_id": "c1f4d1da-fb5c-4ac0-9969-1452096b2b44", "ts": "2026-06-19T17:02:27Z", "source": "portfolio_state", "cycle_id": "20260619T1702Z", "retry_count": 0, "schema_version": "1.0"}, "data": {"equity_usd": 1386.396664, "cash_usd": 1386.396664, "positions": {"BTC": {"symbol": "BTC", "qty": 2e-05, "entry_px": 63127.5, "mark_px": 62939.5, "unreal_pnl": -0.00373, "side": "LONG"}, "ETH": {"symbol": "ETH", "qty": 0.0812, "entry_px": 1703.34, "mark_px": 1697.05, "unreal_pnl": -0.515575, "side": "LONG"}, "SOL": {"symbol": "SOL", "qty": 0.02, "entry_px": 69.363, "mark_px": 68.7385, "unreal_pnl": -0.0125, "side": "LONG"}, "ADA": {"symbol": "ADA", "qty": 0.0, "entry_px": 0.0, "mark_px": 0.16042, "unreal_pnl": 0.0, "side": "FLAT"}, "DOGE": {"symbol": "DOGE", "qty": 0.0, "entry_px": 0.0, "mark_px": 0.082915, "unreal_pnl": 0.0, "side": "FLAT"}}, "open_orders": [], "health": {"last_reconcile_ts": "2026-06-19T17:02:28Z", "reconcile_ok": true, "mode": "live_exchange"}}}

