# V17 live watchdog missing execution closed-loop evidence

- ts_utc: 20260619T210608Z
- git_branch: fix/v17-execution-ctl-mode-regression-test
- git_sha: 936e007
- runtime_boundary: real-money path; execution was not restarted

## Watchdog input

```text
🚨 hl-ai-trader live watchdog 异常
- missing process: services.execution.app
- 订单流: {"alpha.target": 0, "dlq.alpha.target": 0, "dlq.risk.approved.v17_live": 0, "exec.orders": 0, "exec.reports.v17_live": 31, "risk.approved.v17_live": 5459}
- 进程: {"services.execution.app": false, "services.market_data.app": true, "services.risk_engine.app": true}
```

## Safety actions

- Refreshed the runtime stop/freeze control through `scripts/send_ctl_command.py` at 20260619T210618Z.
- Verified `ctl.mode` is a plain string stop/freeze value.
- Verified latest `risk.approved.v17_live` entries are stop/freeze mode with `decision_action=HOLD` and `approved_targets=[]`.
- Did not place orders, cancel orders, flatten positions, or restart execution.

## Process summary

```text
services.execution.app: not running
services.market_data.app: running, pid 65109
services.risk_engine.app: running, pid 91251
```

## Redis summary

```text
Redis ping: PONG
ctl.mode type: string
latest.state.snapshot type=string strlen=977
alpha.target type=stream xlen=0
risk.approved.v17_live type=stream xlen=5460
exec.orders type=none
exec.reports.v17_live type=stream xlen=31
dlq.risk.approved.v17_live type=none
dlq.alpha.target type=none
exec.orders consumer groups: no such key
exec.reports.v17_live group state_reports_grp: pending=0, lag=0
```

## Latest risk evidence

Latest sampled `risk.approved.v17_live` entries after the watchdog incident were verified as stop/freeze mode, `decision_action=HOLD`, and `approved_targets=[]`. One earlier sampled rejection was `live_target_real_money_not_allowed`, also fail-closed.

## Execution stream evidence

`exec.orders` is absent/empty. Latest `exec.reports.v17_live` entries are heartbeat ACK reports with `raw.event=heartbeat`, `skip_reason=skip_decision_action`, and stop/freeze mode; these are not order-placement reports.

## Diagnostics

```text
./.venv/bin/python -m pytest tests/test_execution_control.py -q
.. [100%]

./.venv/bin/python -m pytest tests/scripts/test_hyperliquid_live_probe.py -q
.. [100%]
```

## Source inspection

`services/execution/app.py:get_control_mode` contains the plain-string fallback path via raw Redis client access (`bus.r.get`) after `bus.get_json` parse failure. This matches the suspected stale-runtime scenario rather than a new source defect.

## Exchange truth

A read-only Hyperliquid probe was attempted without any order-placement flag. It failed with HTTP 422 before an account summary could be collected. Therefore exchange positions/open orders are not verified in this run; no secrets were recorded.
