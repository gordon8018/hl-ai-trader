# V17 live watchdog missing execution closed-loop evidence

- timestamp_utc: 20260619T193421Z
- branch: fix/v17-execution-ctl-mode-regression-test
- git_sha: 53bf8e03f2427001ebd973c81417b8c965106a5d
- runtime_boundary: real-money path; execution not restarted by cron
- watchdog_input: missing process services.execution.app; risk_engine and market_data present; risk.approved.v17_live=4908; exec.reports.v17_live=31

## Safety state
```text
redis_ping=PONG
ctl.mode.type=string
ctl.mode.value=HALT
```

## Process summary
```text
65109 48366 /Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m services.market_data.app
91251 48366 .venv/bin/python -m services.risk_engine.app
```

## Redis key and stream summary
```text
latest.state.snapshot type=string len=978
alpha.target type=stream len=0
risk.approved.v17_live type=stream len=4911
exec.orders type=none len=0
exec.reports.v17_live type=stream len=31
dlq.risk.approved.v17_live type=none len=0
dlq.alpha.target type=none len=0
```

## Latest risk approval
```text
1781897660543-0
p
{"env":{"event_id":"c57c91f5-ab17-4a3b-83ec-0814c720fe4a","ts":"2026-06-19T19:34:20Z","source":"risk_engine","cycle_id":"20260619T1933Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T19:33:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[{"symbol":"*","reason":"live_target_real_money_not_allowed","original_weight":0.0,"approved_weight":0.0}],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.010541393428515407,"peak_equity_usd":1386.545559,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.7201544828242784,"basis_abs_avg":0.0,"oi_change_abs_max":0.0010175383349571021}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":62947.5,"mark_px_ETH":1698.95,"mark_px_SOL":68.792,"mark_px_ADA":0.16046,"mark_px_DOGE":0.082723}}}
```

## Latest execution reports
```text
1781885025058-0
p
{"env":{"event_id":"53eac243-f133-44c8-892f-2fa8fb9ab259","ts":"2026-06-19T16:03:45Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884989561-0
p
{"env":{"event_id":"5ee4f6d0-d1d5-4d2a-89a2-e2e508d5fcd2","ts":"2026-06-19T16:03:09Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
1781884984236-0
p
{"env":{"event_id":"c15fb55d-6538-4344-8614-bad9edcae60b","ts":"2026-06-19T16:03:04Z","source":"execution","cycle_id":"20260619T1602Z","retry_count":0,"schema_version":"1.0"},"data":{"client_order_id":"20260619T1602Z:heartbeat","exchange_order_id":null,"symbol":"BTC","status":"ACK","filled_qty":0.0,"avg_px":0.0,"fee":0.0,"latency_ms":0,"raw":{"event":"heartbeat","skip_reason":"skip_decision_action","mode":"HALT"}}}
```
