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

## Safety
- Writes only to: `ctl.commands`, `audit.logs`, `ops.proposals`
- Reads only from allowlisted streams (env: `OPS_ALLOWED_READ_STREAMS`)
- No access to private keys or execution logic
