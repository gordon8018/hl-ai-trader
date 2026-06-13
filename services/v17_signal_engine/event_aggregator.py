from __future__ import annotations

from dataclasses import dataclass

from shared.v17.schemas import V17SignalEvent, V17SignalLeg, V17Side, V17Status
from services.v17_data_builder.features import build_feature_snapshot
from services.v17_data_builder.ohlcv_cache import OHLCVReadResult
from services.v17_signal_engine.quality import score_quality

DEFAULT_EVENT_GROSS_CAP = 0.06
SHADOW_STRATEGY_VERSION = "V17_shadow"


@dataclass(frozen=True)
class V17CandidateSignalResult:
    event: V17SignalEvent

    @property
    def status(self) -> V17Status:
        return self.event.status

    @property
    def reason(self) -> str:
        return self.event.reason

    def to_event(self) -> V17SignalEvent:
        return self.event

    def to_dict(self) -> dict[str, object]:
        return self.event.to_dict()


def build_candidate_signal(
    read_result: OHLCVReadResult,
    *,
    side: V17Side,
    event_gross_cap: float = DEFAULT_EVENT_GROSS_CAP,
) -> V17CandidateSignalResult:
    feature_result = build_feature_snapshot(read_result, side=side)
    snapshot = feature_result.to_snapshot()
    event_id = _event_id(snapshot.symbol, side, snapshot.entry_time_utc, snapshot.bar_close_time_utc)

    if snapshot.status != "valid":
        reason = snapshot.reason or "invalid_feature_snapshot"
        return V17CandidateSignalResult(
            event=_invalid_event(
                event_id=event_id,
                symbol=snapshot.symbol,
                side=side,
                entry_time_utc=snapshot.entry_time_utc,
                bar_close_time_utc=snapshot.bar_close_time_utc,
                event_gross_cap=event_gross_cap,
                reason=reason,
            )
        )

    quality = score_quality(snapshot)
    if quality.status != "valid" or quality.quality_score is None:
        reason = quality.reason or "quality_score_unavailable"
        return V17CandidateSignalResult(
            event=_invalid_event(
                event_id=event_id,
                symbol=snapshot.symbol,
                side=side,
                entry_time_utc=snapshot.entry_time_utc,
                bar_close_time_utc=snapshot.bar_close_time_utc,
                event_gross_cap=event_gross_cap,
                reason=reason,
            )
        )

    cap_multiplier = quality.event_gross_cap_multiplier
    raw_leg_weight = event_gross_cap * cap_multiplier
    final_leg_weight = _signed_weight(raw_leg_weight, side)
    leg = V17SignalLeg(
        event_id=event_id,
        leg_id=f"{event_id}:leg:0",
        symbol=snapshot.symbol,
        side=side,
        quality_score=quality.quality_score,
        quality_label=quality.quality_label,
        raw_leg_weight=raw_leg_weight,
        final_leg_weight=final_leg_weight,
        status="valid",
        reason=f"shadow_candidate:{quality.reason}:cap_multiplier={cap_multiplier:.6f}",
    )
    return V17CandidateSignalResult(
        event=V17SignalEvent(
            event_id=event_id,
            entry_time_utc=snapshot.entry_time_utc,
            bar_close_time_utc=snapshot.bar_close_time_utc,
            event_gross_cap=event_gross_cap,
            legs=[leg],
            status="valid",
            reason="shadow_candidate_ready",
            strategy_version=SHADOW_STRATEGY_VERSION,
        )
    )


def _invalid_event(
    *,
    event_id: str,
    symbol: str,
    side: V17Side,
    entry_time_utc: str,
    bar_close_time_utc: str,
    event_gross_cap: float,
    reason: str,
) -> V17SignalEvent:
    leg = V17SignalLeg(
        event_id=event_id,
        leg_id=f"{event_id}:leg:0",
        symbol=symbol,
        side=side,
        quality_score=None,
        quality_label="UNAVAILABLE",
        raw_leg_weight=0.0,
        final_leg_weight=0.0,
        status="invalid",
        reason=reason,
    )
    return V17SignalEvent(
        event_id=event_id,
        entry_time_utc=entry_time_utc,
        bar_close_time_utc=bar_close_time_utc,
        event_gross_cap=event_gross_cap,
        legs=[leg],
        status="invalid",
        reason=reason,
        strategy_version=SHADOW_STRATEGY_VERSION,
    )


def _signed_weight(raw_leg_weight: float, side: V17Side) -> float:
    if side == "SHORT":
        return -raw_leg_weight
    if side == "FLAT":
        return 0.0
    return raw_leg_weight


def _event_id(symbol: str, side: V17Side, entry_time_utc: str, bar_close_time_utc: str) -> str:
    signal_ts = entry_time_utc or "invalid_entry_time"
    bar_ts = bar_close_time_utc or "invalid_bar_close_time"
    return f"v17-shadow:{symbol}:{side}:{bar_ts}:{signal_ts}"
