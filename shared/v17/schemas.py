from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TargetPortfolio, TargetWeight

V17Side = Literal["LONG", "SHORT", "FLAT"]
V17QualityLabel = Literal["Q1", "Q2", "Q3", "Q4", "UNAVAILABLE"]
V17Status = Literal["valid", "invalid", "blocked"]


class V17SerializableModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]):
        return cls.model_validate(payload)


class V17FeatureSnapshot(V17SerializableModel):
    symbol: str
    bar_close_time_utc: str
    entry_time_utc: str
    side: V17Side
    features: Dict[str, float] = Field(default_factory=dict)
    status: V17Status = "valid"
    reason: str = ""


class V17SignalLeg(V17SerializableModel):
    event_id: str
    leg_id: str
    symbol: str
    side: V17Side
    quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    quality_label: V17QualityLabel
    raw_leg_weight: float
    final_leg_weight: float
    status: V17Status = "valid"
    reason: str = ""

    @model_validator(mode="after")
    def validate_weight_sign(self) -> "V17SignalLeg":
        if self.side == "LONG" and self.final_leg_weight < 0:
            raise ValueError("LONG final_leg_weight must be non-negative")
        if self.side == "SHORT" and self.final_leg_weight > 0:
            raise ValueError("SHORT final_leg_weight must be non-positive")
        if self.side == "FLAT" and self.final_leg_weight != 0:
            raise ValueError("FLAT final_leg_weight must be zero")
        return self


class V17SignalEvent(V17SerializableModel):
    event_id: str
    entry_time_utc: str
    bar_close_time_utc: str
    event_gross_cap: float = Field(ge=0.0)
    legs: List[V17SignalLeg] = Field(default_factory=list)
    status: V17Status = "valid"
    reason: str = ""
    strategy_version: str = "v17"

    @model_validator(mode="after")
    def validate_leg_event_ids(self) -> "V17SignalEvent":
        invalid = [leg.leg_id for leg in self.legs if leg.event_id != self.event_id]
        if invalid:
            raise ValueError(f"legs.event_id mismatch: {invalid}")
        return self

    def to_target_portfolio(self) -> TargetPortfolio:
        targets = [
            TargetWeight(symbol=leg.symbol, weight=leg.final_leg_weight)
            for leg in self.legs
            if leg.status == "valid"
        ]
        universe = [leg.symbol for leg in self.legs]
        gross_weight = sum(abs(target.weight) for target in targets)
        return TargetPortfolio(
            asof_minute=self.entry_time_utc,
            universe=universe,
            targets=targets,
            cash_weight=max(0.0, 1.0 - gross_weight),
            confidence=1.0,
            rationale=self.reason,
            model={"name": "v17", "version": self.strategy_version},
            constraints_hint={"event_gross_cap": self.event_gross_cap},
            decision_horizon="1h",
            major_cycle_id=self.event_id,
            decision_action="REBALANCE" if targets else "HOLD",
            evidence=self.to_dict(),
        )


class V17LedgerRow(V17SerializableModel):
    event_id: str
    leg_id: str
    entry_time_utc: str
    bar_close_time_utc: str
    symbol: str
    side: V17Side
    quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    quality_label: V17QualityLabel
    event_gross_cap: float = Field(ge=0.0)
    raw_leg_weight: float
    final_leg_weight: float
    status: V17Status
    reason: str = ""
    strategy_version: str = "v17"
