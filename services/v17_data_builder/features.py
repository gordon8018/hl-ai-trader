from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Sequence

from shared.v17.schemas import V17FeatureSnapshot, V17Side

from services.v17_data_builder.ohlcv_cache import OHLCVBar, OHLCVReadResult

DEFAULT_RANK_WINDOW = 5
MIN_HISTORY_BARS = DEFAULT_RANK_WINDOW + 1


class FeatureBuilderError(ValueError):
    """Raised when V17 features cannot be built safely."""


class InsufficientFeatureHistoryError(FeatureBuilderError):
    """Raised when too few completed bars are available for V17 features."""


@dataclass(frozen=True)
class V17FeatureBuildResult:
    snapshot: V17FeatureSnapshot

    @property
    def status(self) -> Literal["valid", "invalid", "blocked"]:
        return self.snapshot.status

    @property
    def reason(self) -> str:
        return self.snapshot.reason

    def to_snapshot(self) -> V17FeatureSnapshot:
        return self.snapshot

    def to_dict(self) -> dict[str, object]:
        return self.snapshot.to_dict()


def build_feature_snapshot(
    read_result: OHLCVReadResult,
    *,
    side: V17Side,
    entry_delay: timedelta = timedelta(hours=1),
    rank_window: int = DEFAULT_RANK_WINDOW,
    raise_on_invalid: bool = False,
) -> V17FeatureBuildResult:
    symbol = read_result.symbol.strip().upper()
    required_history = rank_window + 1
    if read_result.status != "valid":
        return _invalid_result(
            symbol,
            side,
            read_result.reason or "invalid_ohlcv",
            raise_on_invalid,
        )
    if len(read_result.bars) < required_history:
        return _invalid_result(symbol, side, "insufficient_history", raise_on_invalid)

    bars = tuple(read_result.bars)
    signal_bar = bars[-1]
    bar_close_time_utc = _format_utc(signal_bar.timestamp)
    entry_time_utc = _format_utc(signal_bar.timestamp + entry_delay)

    ranges = [_bar_range(bar) for bar in bars]
    volumes = [bar.volume for bar in bars]
    true_ranges = _true_ranges(bars)
    signal_range = ranges[-1]
    signal_volume = volumes[-1]
    signal_momentum = _momentum(bars[-2], signal_bar)
    side_momentum = _side_adjusted(signal_momentum, side)
    historical_side_momentum = [
        _side_adjusted(_momentum(previous, current), side)
        for previous, current in zip(bars[:-1], bars[1:])
    ]

    window_ranges = ranges[-rank_window:]
    window_volumes = volumes[-rank_window:]
    window_true_ranges = true_ranges[-rank_window:]
    window_momentum = historical_side_momentum[-rank_window:]
    atr = sum(window_true_ranges) / rank_window

    snapshot = V17FeatureSnapshot(
        symbol=symbol,
        bar_close_time_utc=bar_close_time_utc,
        entry_time_utc=entry_time_utc,
        side=side,
        features={
            "atr": atr,
            "atr_pct": atr / signal_bar.close if signal_bar.close else 0.0,
            "bar_range": signal_range,
            "bar_range_pct": signal_range / signal_bar.close if signal_bar.close else 0.0,
            "range_rank": _percentile_rank(signal_range, window_ranges),
            "volume": signal_volume,
            "volume_rank": _percentile_rank(signal_volume, window_volumes),
            "momentum_1h": signal_momentum,
            "side_momentum_1h": side_momentum,
            "side_momentum_rank": _percentile_rank(side_momentum, window_momentum),
        },
        status="valid",
        reason="quality_inputs_ready",
    )
    return V17FeatureBuildResult(snapshot=snapshot)


def build_invalid_feature_snapshot(
    symbol: str,
    *,
    side: V17Side,
    reason: str,
) -> V17FeatureSnapshot:
    normalized_symbol = symbol.strip().upper()
    return V17FeatureSnapshot(
        symbol=normalized_symbol,
        bar_close_time_utc="",
        entry_time_utc="",
        side=side,
        features={},
        status="invalid",
        reason=reason,
    )


def _invalid_result(
    symbol: str,
    side: V17Side,
    reason: str,
    raise_on_invalid: bool,
) -> V17FeatureBuildResult:
    if raise_on_invalid:
        raise InsufficientFeatureHistoryError(reason)
    return V17FeatureBuildResult(
        snapshot=build_invalid_feature_snapshot(symbol, side=side, reason=reason)
    )


def _bar_range(bar: OHLCVBar) -> float:
    return bar.high - bar.low


def _momentum(previous: OHLCVBar, current: OHLCVBar) -> float:
    if previous.close == 0:
        raise FeatureBuilderError("previous close cannot be zero")
    return (current.close - previous.close) / previous.close


def _side_adjusted(momentum: float, side: V17Side) -> float:
    if side == "SHORT":
        return -momentum
    if side == "FLAT":
        return 0.0
    return momentum


def _true_ranges(bars: Sequence[OHLCVBar]) -> list[float]:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        candidates = [bar.high - bar.low]
        if previous_close is not None:
            candidates.extend([abs(bar.high - previous_close), abs(bar.low - previous_close)])
        true_ranges.append(max(candidates))
        previous_close = bar.close
    return true_ranges


def _percentile_rank(value: float, window: Sequence[float]) -> float:
    if not window:
        raise FeatureBuilderError("rank window cannot be empty")
    below = sum(1 for item in window if item < value)
    equal = sum(1 for item in window if item == value)
    return (below + 0.5 * equal) / len(window)


def _format_utc(value) -> str:
    return value.isoformat().replace("+00:00", "Z")
