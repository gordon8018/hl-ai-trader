from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Dict, List, Literal, Optional
import re
import uuid
from datetime import datetime, timezone

_CYCLE_RE = re.compile(r"^\d{8}T\d{4}Z$")  # YYYYMMDDTHHMMZ

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def current_cycle_id(ts: Optional[datetime] = None) -> str:
    """UTC minute cycle id like 20260218T1600Z."""
    dt = ts or datetime.now(timezone.utc)
    dt = dt.replace(second=0, microsecond=0)
    return dt.strftime("%Y%m%dT%H%MZ")

def cycle_id_from_asof_minute(asof_minute: str) -> str:
    """
    Accept ISO minute string like '2026-02-18T16:00:00Z' or '2026-02-18T16:00Z'
    and produce '20260218T1600Z'.
    """
    s = asof_minute.strip()
    # normalize
    if s.endswith("Z"):
        s2 = s[:-1]
    else:
        s2 = s
    # allow 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DDTHH:MM:SS'
    # parse defensively
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(s2, fmt).replace(tzinfo=timezone.utc)
            dt = dt.replace(second=0, microsecond=0)
            return dt.strftime("%Y%m%dT%H%MZ")
        except ValueError:
            continue
    raise ValueError(f"Invalid asof_minute: {asof_minute}")

class Envelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = Field(default_factory=now_iso)
    source: str
    cycle_id: str  # MUST be present
    retry_count: int = 0
    schema_version: str = "1.0"

    @field_validator("cycle_id")
    @classmethod
    def validate_cycle_id(cls, v: str) -> str:
        if not isinstance(v, str) or not _CYCLE_RE.match(v):
            raise ValueError(f"cycle_id must match YYYYMMDDTHHMMZ, got: {v}")
        return v

class FeatureSnapshot1m(BaseModel):
    asof_minute: str
    universe: List[str]
    mid_px: Dict[str, float]
    ret_1m: Dict[str, float] = Field(default_factory=dict)
    ret_5m: Dict[str, float] = Field(default_factory=dict)
    ret_1h: Dict[str, float] = Field(default_factory=dict)
    vol_1h: Dict[str, float] = Field(default_factory=dict)
    spread_bps: Dict[str, float] = Field(default_factory=dict)
    book_imbalance: Dict[str, float] = Field(default_factory=dict)
    liq_intensity: Dict[str, float] = Field(default_factory=dict)
    liquidity_score: Dict[str, float] = Field(default_factory=dict)

class FeatureSnapshot15m(BaseModel):
    asof_minute: str
    window_start_minute: str
    universe: List[str]
    mid_px: Dict[str, float]
    ret_15m: Dict[str, float] = Field(default_factory=dict)
    ret_30m: Dict[str, float] = Field(default_factory=dict)
    ret_1h: Dict[str, float] = Field(default_factory=dict)
    vol_15m: Dict[str, float] = Field(default_factory=dict)
    vol_1h: Dict[str, float] = Field(default_factory=dict)
    spread_bps: Dict[str, float] = Field(default_factory=dict)
    liquidity_score: Dict[str, float] = Field(default_factory=dict)

class Position(BaseModel):
    symbol: str
    qty: float
    entry_px: float
    mark_px: float
    unreal_pnl: float
    side: Literal["LONG", "SHORT", "FLAT"] = "FLAT"

class OpenOrder(BaseModel):
    client_order_id: str
    exchange_order_id: Optional[str] = None
    symbol: str
    side: Literal["BUY", "SELL"]
    px: float
    qty: float
    status: str

class StateSnapshot(BaseModel):
    equity_usd: float
    cash_usd: float
    positions: Dict[str, Position]
    open_orders: List[OpenOrder] = Field(default_factory=list)
    health: Dict[str, Any] = Field(default_factory=dict)

class TargetWeight(BaseModel):
    symbol: str
    weight: float

class TargetPortfolio(BaseModel):
    asof_minute: str
    universe: List[str]
    targets: List[TargetWeight]
    cash_weight: float
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=800)
    model: Dict[str, str] = Field(default_factory=lambda: {"name": "baseline", "version": "v1"})
    constraints_hint: Dict[str, float] = Field(default_factory=dict)
    decision_horizon: str = "1m"
    major_cycle_id: Optional[str] = None
    decision_action: Literal["REBALANCE", "HOLD", "PROTECTIVE_ONLY"] = "REBALANCE"
    evidence: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_symbols_in_universe(self) -> "TargetPortfolio":
        universe = set(self.universe or [])
        targets = self.targets or []
        invalid = [t.symbol for t in targets if t.symbol not in universe]
        if invalid:
            raise ValueError(f"targets.symbol not in universe: {invalid}")
        return self

class Rejection(BaseModel):
    symbol: str
    reason: str
    original_weight: float
    approved_weight: float

class ApprovedTargetPortfolio(BaseModel):
    asof_minute: str
    mode: Literal["NORMAL", "REDUCE_ONLY", "HALT"] = "NORMAL"
    decision_action: Literal["REBALANCE", "HOLD", "PROTECTIVE_ONLY"] = "REBALANCE"
    approved_targets: List[TargetWeight]
    rejections: List[Rejection] = Field(default_factory=list)
    risk_summary: Dict[str, Any] = Field(default_factory=dict)

class SliceOrder(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    order_type: Literal["LIMIT", "IOC", "ALO"] = "LIMIT"
    px_guard_bps: int = 10
    timeout_s: int = 20
    slice_idx: int = 0

class ExecutionLimits(BaseModel):
    max_orders_per_min: int
    max_cancels_per_min: int

class ExecutionPlan(BaseModel):
    cycle_id: str
    plan_type: Literal["TWAP_REBALANCE"] = "TWAP_REBALANCE"
    slices: List[SliceOrder]
    limits: ExecutionLimits
    idempotency_key: str

class OrderIntent(BaseModel):
    intent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: Literal["PLACE", "CANCEL", "REPLACE"]
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    order_type: Literal["LIMIT", "IOC", "ALO"] = "LIMIT"
    limit_px: Optional[float] = None
    tif: Optional[str] = None
    client_order_id: str
    slice_ref: Optional[str] = None
    reason: str = ""

class ExecutionReport(BaseModel):
    client_order_id: str
    exchange_order_id: Optional[str] = None
    symbol: str
    status: Literal["ACK", "PARTIAL", "FILLED", "CANCELED", "REJECTED"]
    filled_qty: float = 0.0
    avg_px: float = 0.0
    fee: float = 0.0
    latency_ms: int = 0
    raw: Optional[dict] = None

class Message(BaseModel):
    env: Envelope
    data: Any
    event: Optional[str] = None
