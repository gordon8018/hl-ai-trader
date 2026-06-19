# V17 live watchdog missing execution closed-loop evidence

- timestamp_utc: 2026-06-19T18:51:21Z
- repo: /Users/gordon8018/workspace/hl-ai-trader
- branch: fix/v17-execution-ctl-mode-regression-test
- incident: watchdog reported missing `services.execution.app`

## Watchdog input

```text
🚨 hl-ai-trader live watchdog 异常
- missing process: services.execution.app
- 订单流: {"alpha.target": 0, "dlq.alpha.target": 0, "dlq.risk.approved.v17_live": 0, "exec.orders": 0, "exec.reports.v17_live": 31, "risk.approved.v17_live": 4652}
- 进程: {"services.execution.app": false, "services.market_data.app": true, "services.risk_engine.app": true}
```

## Safety actions

- Sent HALT via `./.venv/bin/python scripts/send_ctl_command.py --cmd HALT --reason "watchdog incident 20260619T185134Z" --source ops.cron.closed_loop`.
- Verified `ctl.mode` type is `string` and value is `HALT`.
- Verified latest `risk.approved.v17_live` entries are `mode=HALT`, `decision_action=HOLD`, `approved_targets=[]`.
- Did not start execution and did not place/cancel/flatten any orders.

## Runtime evidence

- Redis `PING`: PONG.
- Processes: `services.market_data.app` running; `services.risk_engine.app` running; `services.execution.app` not found.
- Key/stream types: `latest.state.snapshot=string`, `ctl.mode=string`, `alpha.target=stream`, `risk.approved.v17_live=stream`, `exec.orders=none`, `exec.reports.v17_live=stream`, `dlq.risk.approved.v17_live=none`, `dlq.alpha.target=none`.
- Stream lengths after HALT: `alpha.target=0`, `risk.approved.v17_live=4656`, `exec.orders=0`, `exec.reports.v17_live=31`, `dlq.risk.approved.v17_live=0`, `dlq.alpha.target=0`.
- `exec.orders` consumer group `exec_grp`: not present because stream key is absent.
- Latest execution reports are old heartbeat ACK reports with `raw.event=heartbeat`, `skip_reason=skip_decision_action`, `mode=HALT` at about 2026-06-19T16:03Z.
- Latest snapshot health: `health.reconcile_ok=true`, `health.mode=live_exchange`, open_orders empty. Snapshot includes small live positions from portfolio_state; Redis/local snapshot is not exchange truth.

## Root-cause evidence

- `logs/live/execution.log` shows `services.execution.app` crashed with `json.decoder.JSONDecodeError` while reading `ctl.mode` through `bus.get_json(CTL_MODE_KEY)` when Redis held plain string `HALT`.
- Current source has `get_control_mode()` fallback to raw Redis `bus.r.get(CTL_MODE_KEY)` after JSON parse errors.
- Current regression `tests/test_execution_ctl_commands.py::test_get_control_mode_accepts_plain_string_redis_value_after_json_error` covers plain-string `HALT` fallback.

## Verification

- Ran `./.venv/bin/python -m pytest tests/test_execution_ctl_commands.py -q`.
- Result: `5 passed`.

## Recovery gate

- Live execution remains stopped.
- Recovery is blocked on PR/merge/runtime restart gate and Gordon confirmation before starting real-money execution or consuming any non-empty `NORMAL/REBALANCE` approval.
