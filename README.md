# hl-ai-trader (Redis Streams)

## What it is
Minute-level portfolio trading skeleton for Hyperliquid, with async order tracking and rate-aware polling:

`market_data -> ai_decision -> risk_engine -> execution`

plus `portfolio_state` reconciliation and `reporting` (SQLite-based trade/PnL store), using Redis Streams as event bus.

## Architecture streams
- `md.features.1m`
- `md.features.15m`
- `state.snapshot`
- `alpha.target`
- `risk.approved`
- `exec.plan`
- `exec.orders`
- `exec.reports`
- `exec.order_tracking_queue` (Redis list for async order tracking)
- `audit.logs`
- `ctl.commands`

## System architecture diagram
```
market_data
   |
   v
ai_decision  --->  alpha.target
   |
   v
risk_engine  --->  risk.approved
   |
   v
execution  --->  exec.plan / exec.orders / exec.reports
   |
   v
AsyncOrderTracker (thread)
   |
   v
exec.order_tracking_queue  --->  order_status polling / cancel

portfolio_state  --->  state.snapshot
reporting        --->  reporting.db
ctl.commands     --->  execution control
audit.logs       --->  cross-service audit trail
```

## Service responsibilities
- `market_data`: pull market snapshots, microstructure, execution feedback; publish feature streams.
- `ai_decision`: build target weights (rule-based + optional LLM), output `alpha.target`.
- `risk_engine`: apply risk caps and execution health gating; output `risk.approved`.
- `execution`: slice orders, submit to exchange, emit ACK/terminal reports.
- `portfolio_state`: reconcile account state, maintain `state.snapshot`, poll active orders.
- `reporting`: persist reports and PnL into `reporting.db`.
- `shared.bus`: Redis Streams helpers, DLQ handling, basic audit events.

## Data flow (high level)
1. `market_data` emits `md.features.1m` / `md.features.15m`.
2. `ai_decision` reads features, emits `alpha.target`.
3. `risk_engine` consumes `alpha.target`, emits `risk.approved`.
4. `execution` consumes `risk.approved`, emits `exec.plan`, `exec.orders`, `exec.reports`.
5. `portfolio_state` updates `state.snapshot` and tracks active order status.
6. `reporting` consumes `exec.reports` and writes `reporting.db`.

## Quick start (Docker Compose)
1) Prepare env file:
```bash
cp infra/env.example infra/.env
```

2) Fill required keys in `infra/.env`:
- `HL_ACCOUNT_ADDRESS` (main account address)
- `HL_PRIVATE_KEY` (only required when `DRY_RUN=false`)
- Keep `DRY_RUN=true` first

3) Start:
```bash
cd infra
docker compose up --build
```

## Quick start (local process mode, recommended for debugging)
This mode writes logs and pid files under:
- `logs/local/*.log`
- `run/local/*.pid`

1) Start Redis locally (example):
```bash
docker run --name hl-redis -p 6379:6379 -d redis:7
```

2) Set env (example):
```bash
export REDIS_URL=redis://127.0.0.1:6379/0
export HL_HTTP_URL=https://api.hyperliquid.xyz
export DRY_RUN=true
export UNIVERSE=BTC,ETH,SOL,ADA,DOGE
export HL_ACCOUNT_ADDRESS=<your_main_address>
export METRICS_ENABLED=false
```

3) Start all services:
```bash
bash scripts/run_local.sh start
```

4) Check status/logs:
```bash
bash scripts/run_local.sh status
bash scripts/run_local.sh logs
tail -f logs/local/execution.log
```

5) Stop all services:
```bash
bash scripts/run_local.sh stop
```

## Scripts usage

### 1) `scripts/run_local.sh`
Command format:
```bash
bash scripts/run_local.sh <start|stop|restart|status|logs>
```

Key env variables:
- `REDIS_URL` default `redis://127.0.0.1:6379/0`
- `HL_HTTP_URL` default `https://api.hyperliquid.xyz`
- `DRY_RUN` default `true`
- `UNIVERSE` default `BTC,ETH,SOL,ADA,DOGE`
- `HL_ACCOUNT_ADDRESS` required for `state.snapshot`
- `HL_PRIVATE_KEY` required when `DRY_RUN=false`
- `METRICS_ENABLED` default `false`
- `RESET_CTL_MODE` default `true` (auto-clear `ctl.mode` on start)
- `MD_PERP_CTX_POLL_SECONDS` default `10.0` (market_data pull interval for `metaAndAssetCtxs`)
- `MD_PERP_CTX_STALE_SECONDS` default `120.0` (audit threshold for stale perp context cache)
- `MD_L2_ENABLED` default `true` (enable `l2Book` snapshot polling)
- `MD_L2_POLL_SECONDS` default `10.0` (market_data pull interval for `l2Book`)
- `MD_L2_STALE_SECONDS` default `120.0` (audit threshold for stale L2 cache)
- `MD_L2_N_SIGFIGS` optional (valid: `2|3|4|5`)
- `MD_L2_MANTISSA` optional (only with `MD_L2_N_SIGFIGS=5`)
- `MD_EXEC_REPORT_SCAN_LIMIT` default `800` (market_data lookback sample size for exec feedback features)
- `MD_TRADES_ENABLED` default `true` (enable REST trades pull)
- `MD_TRADES_WINDOW_SEC` default `60`
- `MD_TRADES_ENDPOINT_TYPE` default `recentTrades`
- `AI_USE_LLM` default `false` (enable strict-JSON LLM candidate path)
- `AI_LLM_MOCK_RESPONSE` optional (when `AI_USE_LLM=true`, provide JSON string for local validation/fallback tests)
- `AI_STREAM_IN` default `md.features.1m` (ai_decision input stream)
- `AI_DECISION_HORIZON` default `15m`
- `AI_LLM_ENDPOINT` optional OpenAI-compatible chat completions endpoint
- `AI_LLM_API_KEY` optional API key for online LLM
- `AI_LLM_MODEL` optional model id for online LLM
- `ORDER_TRACKING_QUEUE` default `exec.order_tracking_queue`
- `MAX_ORDERS_PER_POLL` default `50` (portfolio_state orderStatus poll cap)
- `ORDER_STATUS_BATCH_SIZE` default `10`
- `ORDER_STATUS_ERROR_BASE_BACKOFF_MS` default `500`
- `ORDER_STATUS_MAX_BACKOFF_MS` default `30000`
- `ORDER_STATUS_TTL_SECONDS` default `3600`
- `RISK_MARKET_FEATURE_STREAM` default `md.features.15m`
- `RISK_REJECT_RATE_REDUCE_ONLY` default `0.15`, `RISK_REJECT_RATE_HALT` default `0.35`
- `RISK_P95_LATENCY_MS_REDUCE_ONLY` default `3000`, `RISK_P95_LATENCY_MS_HALT` default `7000`
- `RISK_LIQUIDITY_SCORE_REDUCE_ONLY` default `0.15`, `RISK_LIQUIDITY_SCORE_HALT` default `0.05`
- `RISK_BASIS_BPS_REDUCE_ONLY` default `40`, `RISK_BASIS_BPS_HALT` default `80`
- `RISK_OI_CHANGE_REDUCE_ONLY` default `0.20`, `RISK_OI_CHANGE_HALT` default `0.40`
- `REPORT_DB_PATH` default `data/reporting.db`
- `REPORT_POLL_MS` default `1000`

### 2) `scripts/send_ctl_command.py`
Send control commands into `ctl.commands`:
```bash
./.venv/bin/python scripts/send_ctl_command.py --cmd HALT --reason "manual stop"
./.venv/bin/python scripts/send_ctl_command.py --cmd REDUCE_ONLY --reason "degrade mode"
./.venv/bin/python scripts/send_ctl_command.py --cmd RESUME --reason "recover"
```

Options:
- `--cmd` one of `HALT|REDUCE_ONLY|RESUME` (required)
- `--reason` optional text
- `--source` default `ops.manual`
- `--redis-url` default from `REDIS_URL`

### 3) `scripts/live_smoke_check.py`
Fast health check for pipeline freshness and state:
```bash
./.venv/bin/python scripts/live_smoke_check.py --max-lag-sec 180 --state-max-age-sec 60
```

Common options:
- `--redis-url`
- `--max-lag-sec` stream freshness threshold
- `--state-max-age-sec` max age for `latest.state.snapshot`
- `--report-sample` recent `exec.reports` sample count

Exit code:
- `0`: PASS
- `2`: FAIL

### 4) `scripts/live_acceptance_report.py`
Generate small-capital live acceptance report (markdown):
```bash
./.venv/bin/python scripts/live_acceptance_report.py \
  --window-minutes 30 \
  --max-lag-sec 180 \
  --state-max-age-sec 60 \
  --min-ack 1 \
  --max-rejected 200 \
  --max-error-events 0
```

Output:
- default directory: `docs/reports/`
- optional custom file: `--output docs/reports/my_report.md`
- report includes market feature summary (`funding/basis/OI/microstructure/execution quality`)

Exit code:
- `0`: report PASS
- `2`: report FAIL

### 5) `scripts/watch_market_data.py`
Real-time market snapshot watcher from `md.features.1m`:
```bash
./.venv/bin/python scripts/watch_market_data.py
```

Useful variants:
```bash
./.venv/bin/python scripts/watch_market_data.py --symbols BTC,ETH,SOL
./.venv/bin/python scripts/watch_market_data.py --show-json
./.venv/bin/python scripts/watch_market_data.py --from-start
```

### 6) `scripts/live_alert_check.py`
Check fixed alert thresholds (lag/reject-rate/latency/DLQ):
```bash
./.venv/bin/python scripts/live_alert_check.py \
  --window-minutes 10 \
  --max-lag-sec 180 \
  --max-reject-rate 0.70 \
  --max-p95-latency-ms 5000 \
  --max-dlq-events 0 \
  --max-basis-bps-abs-avg 60 \
  --min-liquidity-score-avg 0.10 \
  --max-slippage-bps-15m-avg 8
```

Exit code:
- `0`: PASS
- `2`: FAIL (threshold breached)

## Observability (Prometheus)
Metrics are exposed via the per-service HTTP metrics port (see each service for default port; enable with `METRICS_ENABLED=true`).

Key metrics added:
- AI: `ai_llm_call_total`, `ai_llm_error_total`, `ai_llm_latency_ms`, `ai_fallback_total`, `ai_confidence`, `ai_gross`, `ai_net`, `ai_turnover`
- Risk: `risk_clip_total`, `risk_net_cap_hits_total`, `risk_gross_cap_hits_total`, `risk_turnover_cap_hits_total`
- Execution: `exec_orders_total`, `exec_reject_total`, `exec_fill_latency_ms`, `exec_rate_limited_total`

## Redis debug commands
Check latest event in one stream:
```bash
redis-cli -h 127.0.0.1 -p 6379 XREVRANGE md.features.1m + - COUNT 1
```

Read new events continuously:
```bash
redis-cli -h 127.0.0.1 -p 6379 XREAD BLOCK 0 STREAMS md.features.1m $
```

Check stream lengths:
```bash
redis-cli -h 127.0.0.1 -p 6379 XLEN md.features.1m
redis-cli -h 127.0.0.1 -p 6379 XLEN exec.reports
```

Check risk control mode:
```bash
redis-cli -h 127.0.0.1 -p 6379 GET ctl.mode
```

## Going live (small capital)
Before real money:
- Keep `DRY_RUN=true` until smoke and acceptance pass repeatedly.
- Use conservative risk limits and small universe.
- Validate `exec.reports` contains expected `ACK`/terminal statuses.
- Confirm `audit.logs` has no sustained `error`/`dlq` spikes.

Then:
- Set `DRY_RUN=false`
- Provide `HL_PRIVATE_KEY`
- Start with very small notional and leverage `1x`
