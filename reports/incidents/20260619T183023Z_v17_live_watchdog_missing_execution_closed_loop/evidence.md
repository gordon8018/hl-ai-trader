# V17 live watchdog missing execution closed-loop incident

timestamp_utc: 20260619T183023Z
branch: fix/v17-execution-ctl-mode-regression-test
sha: c3a240e

## Watchdog stdout
🚨 hl-ai-trader live watchdog 异常
- missing process: services.execution.app
- 订单流: {"alpha.target": 0, "dlq.alpha.target": 0, "dlq.risk.approved.v17_live": 0, "exec.orders": 0, "exec.reports.v17_live": 31, "risk.approved.v17_live": 4524}
- 进程: {"services.execution.app": false, "services.market_data.app": true, "services.risk_engine.app": true}

## Process evidence
```
exact execution process probe: no matching services.execution.app process
execution.pid=59634
ps -p 59634: no process row returned (stale pid file)
market_data process: present per watchdog and ps evidence
risk_engine process: present per watchdog and ps evidence
```

## Redis evidence before/around HALT
```
redis ping: PONG
ctl.mode type=string
ctl.mode value=HALT
latest.state.snapshot type=string
alpha.target type=stream xlen=0
dlq.alpha.target type=none xlen=0
dlq.risk.approved.v17_live type=none xlen=0
risk.approved.v17_live type=stream xlen=4524
exec.orders type=none xlen=0
exec.reports.v17_live type=stream xlen=31
exec.orders consumer groups: ERR no such key
```

Latest observed risk approvals after watchdog input were fail-closed:
```
mode=HALT
decision_action=HOLD
approved_targets=[]
control_mode=HALT
```

Latest execution reports were safety heartbeats only:
```
status=ACK
raw.event=heartbeat
raw.skip_reason=skip_decision_action
raw.mode=HALT
filled_qty=0.0
avg_px=0.0
```

Latest state snapshot summary:
```
equity_usd=1386.823863
cash_usd=1386.823863
positions: BTC qty=0.00002 LONG, ETH qty=0.0812 LONG, SOL qty=0.02 LONG, ADA/DOGE flat
open_orders=[]
health.reconcile_ok=true
health.mode=live_exchange
```

## HALT action 20260619T183023Z
```
./.venv/bin/python scripts/send_ctl_command.py --cmd HALT --reason "watchdog incident 20260619T183023Z missing execution" --source ops.cron.closed_loop
sent ctl.commands id=1781893836600-0 cmd=HALT reason=watchdog incident 20260619T183023Z missing execution
ctl.mode=HALT
fresh risk approval: mode=HALT, decision_action=HOLD, approved_targets=[]
```

## Root-cause evidence
```
logs/live/execution.log shows JSONDecodeError when execution reads plain-string ctl.mode=HALT via bus.get_json(CTL_MODE_KEY).
Current source on branch fix/v17-execution-ctl-mode-regression-test includes get_control_mode fallback to bus.r.get(CTL_MODE_KEY).
Current branch includes regression test test_get_control_mode_accepts_plain_string_redis_value_after_json_error.
```

## Local verification
```
./.venv/bin/python -m pytest tests/test_execution_ctl_commands.py -q
result: passed (5 tests)

./.venv/bin/python -m pytest tests/test_execution_ctl_commands.py tests/test_risk_engine.py -q
result: not checked; tests/test_risk_engine.py does not exist in this checkout
```

## GitHub / PR status
```
gh auth status: not authenticated
PR/CI/mergeability: blocked in cron; no token values inspected or printed
```

## Safety status
```
execution remains stopped
no live execution restart attempted
no orders submitted
real-money recovery remains gated on PR/CI/merge and Gordon explicit confirmation
```
