from __future__ import annotations

from fastapi import Header, HTTPException

from services.openclaw_ops.config import OpsConfig


def require_bearer_token(config: OpsConfig, authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    token = authorization.split(" ", 1)[1].strip()
    if not token or token != config.api_key:
        raise HTTPException(status_code=401, detail="invalid_token")
