# V17 live watchdog missing execution closed-loop evidence

## Incident
- timestamp_utc: 20260619T173838Z
- watchdog: missing process: services.execution.app
- watchdog_streams: alpha.target=0, dlq.alpha.target=0, dlq.risk.approved.v17_live=0, exec.orders=0, exec.reports.v17_live=31, risk.approved.v17_live=4218
- watchdog_processes: services.execution.app=false, services.market_data.app=true, services.risk_engine.app=true

## Safety action
- HALT command: `./.venv/bin/python scripts/send_ctl_command.py --cmd HALT --reason "watchdog incident 20260619T173838Z" --source ops.cron.closed_loop`
- HALT result: sent ctl.commands id=1781890728208-0
- verified ctl.mode type: string
- verified ctl.mode value: HALT

## Runtime state after HALT
- Redis ping: PONG
- services.execution.app: not running
- services.market_data.app: running
- services.risk_engine.app: running
- services.portfolio_state.app: running
- V17 alpha process: running on alpha.target.v17_live

## Redis evidence
- latest.state.snapshot type: string
- latest.state.snapshot health: reconcile_ok=true, mode=live_exchange
- latest.state.snapshot open_orders: []
- latest.state.snapshot positions: BTC long dust, ETH long, SOL long, ADA flat, DOGE flat as reported by cached portfolio_state snapshot
- alpha.target type: stream, XLEN=0
- alpha.target.v17_live type: stream, XLEN=4220, latest decision_action=HOLD, targets=[]
- risk.approved.v17_live type: stream, XLEN=4220
- latest risk.approved.v17_live: mode=HALT, decision_action=HOLD, approved_targets=[]
- exec.orders type: none
- exec.reports.v17_live type: stream, XLEN=31, latest report is heartbeat ACK with mode=HALT from 2026-06-19T16:03:45Z
- dlq.risk.approved.v17_live type: none
- dlq.alpha.target type: none
- exec.orders consumer group exec_grp: not present because stream key is absent
- exec.reports.v17_live consumer group: state_reports_grp only, pending=0, lag=0

## Process/log evidence
- run/live/execution.pid: 59634, stale/no matching process
- execution log tail shows crash in old runtime path:
  - services/execution/app.py consume_ctl_commands -> get_control_mode
  - get_control_mode called bus.get_json(ctl.mode)
  - shared/bus/redis_streams.py get_json json.loads("HALT")
  - json.decoder.JSONDecodeError: Expecting value

## Source/branch evidence
- branch: fix/v17-execution-ctl-mode-regression-test
- git SHA: da457e56c1be045b382717eb09af790bc0d489a2
- current source has get_control_mode fallback from bus.get_json exception to raw bus.r.get(ctl.mode)
- focused regression present: tests/test_execution_ctl_commands.py::test_get_control_mode_accepts_plain_string_redis_value_after_json_error

## Root-cause assessment
- verified root cause: stale execution runtime crashed on plain-string ctl.mode=HALT because old code attempted JSON-only parsing.
- current branch contains the minimal code/test fix; execution remains stopped and must not be restarted without real-money recovery gates.

## Safety boundary
- No orders placed.
- No execution restart attempted.
- No secrets read or printed.
- Real-money recovery is blocked pending PR/CI/merge and Gordon confirmation gates.
