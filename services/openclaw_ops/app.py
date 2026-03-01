from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from services.openclaw_ops.audit import build_env, write_audit
from services.openclaw_ops.auth import require_bearer_token
from services.openclaw_ops.config import OpsConfig, load_config
from services.openclaw_ops.models import (
    HealthCheckRequest,
    HealthCheckResponse,
    HealthResponse,
    ProposeParamChangeRequest,
    ProposeParamChangeResponse,
    ReadStatusRequest,
    ReadStatusResponse,
    SetModeRequest,
    SetModeResponse,
)
from services.openclaw_ops.redis_client import RedisClient
from services.openclaw_ops.smoke_check import run_smoke_check

app = FastAPI(title="openclaw_ops", version="1.0")

CONFIG: OpsConfig = load_config()
REDIS = RedisClient(CONFIG.redis_url)


@app.get("/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True)


@app.post("/v1/health_check", response_model=HealthCheckResponse)
def health_check(req: HealthCheckRequest, _: None = Depends(lambda: require_bearer_token(CONFIG))):
    metrics, notes = run_smoke_check(req.max_lag_sec, req.state_max_age_sec, req.report_sample)
    return HealthCheckResponse(passed=bool(metrics.get("pass")), metrics=metrics, notes=notes)


@app.post("/v1/read_status", response_model=ReadStatusResponse)
def read_status(req: ReadStatusRequest, _: None = Depends(lambda: require_bearer_token(CONFIG))):
    for stream in req.streams:
        if stream not in CONFIG.allowed_read_streams:
            raise HTTPException(status_code=403, detail="unauthorized_stream")
    data = {}
    for s in req.streams:
        entries = REDIS.read_last(s, req.count)
        data[s] = {
            "entries": entries,
            "count": len(entries),
        }
    return ReadStatusResponse(data=data)


@app.post("/v1/set_mode", response_model=SetModeResponse)
def set_mode(req: SetModeRequest, _: None = Depends(lambda: require_bearer_token(CONFIG))):
    if "ctl.commands" not in CONFIG.allowed_write_streams or "audit.logs" not in CONFIG.allowed_write_streams:
        raise HTTPException(status_code=403, detail="write_not_allowed")
    if not req.request_id:
        raise HTTPException(status_code=400, detail="request_id_required")

    idem_key = f"ops:idem:{req.request_id}"
    cached = REDIS.get_json(idem_key)
    if cached:
        return SetModeResponse(**cached, deduped=True)

    cmd = req.mode.upper().strip()
    if cmd not in {"HALT", "REDUCE_ONLY", "RESUME"}:
        raise HTTPException(status_code=400, detail="invalid_mode")

    ctl_payload = {
        "env": build_env(req.source),
        "data": {"cmd": cmd, "reason": req.reason, "source": req.source, "request_id": req.request_id, "evidence": req.evidence},
    }
    ctl_stream_id = REDIS.xadd_stream("ctl.commands", ctl_payload)
    audit_stream_id = write_audit(
        REDIS,
        "ops.set_mode",
        {"cmd": cmd, "reason": req.reason, "source": req.source, "request_id": req.request_id, "evidence": req.evidence},
        source=req.source,
    )
    resp = SetModeResponse(ok=True, ctl_stream_id=ctl_stream_id, audit_stream_id=audit_stream_id, deduped=False)
    REDIS.set_if_absent(idem_key, resp.model_dump(), ttl_s=CONFIG.idempotency_ttl)
    return resp


@app.post("/v1/propose_param_change", response_model=ProposeParamChangeResponse)
def propose_param_change(req: ProposeParamChangeRequest, _: None = Depends(lambda: require_bearer_token(CONFIG))):
    if "ops.proposals" not in CONFIG.allowed_write_streams or "audit.logs" not in CONFIG.allowed_write_streams:
        raise HTTPException(status_code=403, detail="write_not_allowed")
    if not req.request_id:
        raise HTTPException(status_code=400, detail="request_id_required")

    idem_key = f"ops:idem:{req.request_id}"
    cached = REDIS.get_json(idem_key)
    if cached:
        return ProposeParamChangeResponse(**cached, deduped=True)

    proposal_stream_id = REDIS.xadd_stream(
        "ops.proposals",
        {
            "env": build_env(req.source),
            "data": {"proposal": req.proposal, "reason": req.reason, "source": req.source, "request_id": req.request_id},
        },
    )
    audit_stream_id = write_audit(
        REDIS,
        "ops.propose_param_change",
        {"proposal": req.proposal, "reason": req.reason, "source": req.source, "request_id": req.request_id},
        source=req.source,
    )
    resp = ProposeParamChangeResponse(ok=True, proposal_stream_id=proposal_stream_id, audit_stream_id=audit_stream_id, deduped=False)
    REDIS.set_if_absent(idem_key, resp.model_dump(), ttl_s=CONFIG.idempotency_ttl)
    return resp


def main() -> None:
    import uvicorn

    uvicorn.run("services.openclaw_ops.app:app", host="127.0.0.1", port=9160, log_level="info")


if __name__ == "__main__":
    main()
