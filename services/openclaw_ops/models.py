from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool = True


class HealthCheckRequest(BaseModel):
    max_lag_sec: int = Field(default=180, ge=1)
    state_max_age_sec: int = Field(default=60, ge=1)
    report_sample: int = Field(default=200, ge=1)


class HealthCheckResponse(BaseModel):
    passed: bool
    metrics: Dict[str, Any]
    notes: List[str]


class ReadStatusRequest(BaseModel):
    streams: List[str]
    count: int = Field(default=1, ge=1, le=200)


class ReadStatusResponse(BaseModel):
    data: Dict[str, Any]


class SetModeRequest(BaseModel):
    mode: str
    reason: str = ""
    source: str = "openclaw_ops"
    request_id: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class SetModeResponse(BaseModel):
    ok: bool
    ctl_stream_id: Optional[str] = None
    audit_stream_id: Optional[str] = None
    deduped: bool = False


class ProposeParamChangeRequest(BaseModel):
    proposal: Dict[str, Any]
    reason: str = ""
    source: str = "openclaw_ops"
    request_id: str


class ProposeParamChangeResponse(BaseModel):
    ok: bool
    proposal_stream_id: Optional[str] = None
    audit_stream_id: Optional[str] = None
    deduped: bool = False
