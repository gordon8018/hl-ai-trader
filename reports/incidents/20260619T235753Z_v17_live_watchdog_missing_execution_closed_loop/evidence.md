# V17 live watchdog missing execution closed-loop evidence

- timestamp_utc: 20260619T235753Z
- branch: fix/v17-execution-ctl-mode-regression-test
- sha: f9dcc08024b2f2f08034dbdfe51fb3ca7405a52a
- status: ## fix/v17-execution-ctl-mode-regression-test...origin/fix/v17-execution-ctl-mode-regression-test

## Watchdog output
```text
🚨 hl-ai-trader live watchdog 异常
- missing process: services.execution.app
- 订单流: {"alpha.target": 0, "dlq.alpha.target": 0, "dlq.risk.approved.v17_live": 0, "exec.orders": 0, "exec.reports.v17_live": 31, "risk.approved.v17_live": 6486}
- 进程: {"services.execution.app": false, "services.market_data.app": true, "services.risk_engine.app": true}
```

## Process evidence
```text
65109 /Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m services.market_data.app
91251 .venv/bin/python -m services.risk_engine.app
65109 48366 /Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python -m services.market_data.app
91251 48366 .venv/bin/python -m services.risk_engine.app
```

## Redis evidence after HALT refresh
```text
PING: PONG
ctl.mode: HALT
ctl.mode type=string
latest.state.snapshot type=string
alpha.target type=stream xlen=0
risk.approved.v17_live type=stream xlen=6492
exec.orders type=none
exec.reports.v17_live type=stream xlen=31
dlq.risk.approved.v17_live type=none
dlq.alpha.target type=none

XINFO GROUPS exec.orders:
ERR no such key


XINFO GROUPS risk.approved.v17_live:
name
exec_grp
consumers
1
pending
0
last-delivered-id
1781885025056-0
entries-read
3649
lag
2843

latest risk.approved.v17_live:
1781913494689-0
p
{"env":{"event_id":"053ee767-78ef-4c97-8114-36a47aff77c5","ts":"2026-06-19T23:58:14Z","source":"risk_engine","cycle_id":"20260619T2357Z","retry_count":0,"schema_version":"1.0"},"data":{"asof_minute":"2026-06-19T23:57:00Z","mode":"HALT","decision_action":"HOLD","approved_targets":[],"rejections":[],"risk_summary":{"gross":0,"net":0,"control_mode":"HALT","daily_loss_ratio":0.009751863517327009,"peak_equity_usd":1387.651941,"peak_drawdown_ratio":0.0,"ic_decay_streak":0,"consecutive_rejected":0,"market_quality_mode":"NORMAL","market_quality_reasons":[],"market_quality_stats":{"reject_rate_avg":0.0,"p95_latency_avg":0.0,"liquidity_avg":0.5740658762424461,"basis_abs_avg":0.0,"oi_change_abs_max":0.0031057860280578886}},"constraints_hint":{"max_gross":0.12,"max_net":0.08,"max_symbol_gross":0.05,"max_event_gross":0.06,"max_daily_turnover":0.18,"max_trades_per_day":6.0,"mark_px_BTC":63565.5,"mark_px_ETH":1712.55,"mark_px_SOL":69.7685,"mark_px_ADA":0.162135,"mark_px_DOGE":0.083611}}}
```

## latest.state.snapshot summary
```text
equity_usd= 1387.651941
cash_usd= 1387.651941
positions_count= 5
open_orders_count= 0
health= {'last_reconcile_ts': '2026-06-19T23:58:09Z', 'reconcile_ok': True, 'mode': 'live_exchange'}
```
