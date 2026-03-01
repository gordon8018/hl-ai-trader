# openclaw_ops

Restricted ops bridge for OpenClaw Trader Agent. **No trading logic.**

## Run
```bash
export REDIS_URL=redis://127.0.0.1:6379/0
export OPS_API_KEY=changeme
python -m services.openclaw_ops.app
```

## Endpoints
- `GET /v1/health`
- `POST /v1/health_check`
- `POST /v1/read_status`
- `POST /v1/set_mode`
- `POST /v1/propose_param_change`

All POST endpoints require `Authorization: Bearer <OPS_API_KEY>`.

## Example Calls

### 1) Liveness
```bash
curl http://127.0.0.1:9160/v1/health
```

### 2) Health Check (smoke_check)
```bash
curl -X POST http://127.0.0.1:9160/v1/health_check \
  -H "Authorization: Bearer $OPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"max_lag_sec":180,"state_max_age_sec":60,"report_sample":200}'
```

### 3) Read Status (allowlisted streams only)
```bash
curl -X POST http://127.0.0.1:9160/v1/read_status \
  -H "Authorization: Bearer $OPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"streams":["state.snapshot","exec.reports"],"count":3}'
```

### 4) Set Mode (idempotent)
```bash
curl -X POST http://127.0.0.1:9160/v1/set_mode \
  -H "Authorization: Bearer $OPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "mode":"HALT",
    "reason":"manual stop",
    "source":"ops.manual",
    "request_id":"req-20260301-0001",
    "evidence":{"ticket":"INC-1234"}
  }'
```

### 5) Propose Param Change (optional)
```bash
curl -X POST http://127.0.0.1:9160/v1/propose_param_change \
  -H "Authorization: Bearer $OPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "proposal":{"MAX_GROSS":0.2},
    "reason":"risk reduction",
    "source":"ops.manual",
    "request_id":"req-20260301-0002"
  }'
```

## Safety
- Writes only to: `ctl.commands`, `audit.logs`, `ops.proposals`
- Reads only from allowlisted streams (env: `OPS_ALLOWED_READ_STREAMS`)
- No access to private keys or execution logic
