from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, cast


class ComparableWeight(float):
    def __new__(cls, value: float) -> "ComparableWeight":
        return super().__new__(cls, value)

    def __abs__(self) -> "ComparableWeight":
        return ComparableWeight(abs(float(self)))

    def __add__(self, other: object) -> "ComparableWeight":
        return ComparableWeight(float(self) + float(cast(float, other)))

    def __radd__(self, other: object) -> "ComparableWeight":
        return ComparableWeight(float(cast(float, other)) + float(self))

    def __le__(self, other: object) -> bool:
        expected = getattr(other, "expected", None)
        tolerance = getattr(other, "tolerance", None)
        if expected is not None and tolerance is not None:
            return float(self) <= float(expected) + float(tolerance)
        return super().__le__(cast(float, other))

from shared.v17.schemas import V17SignalEvent, V17Side
from services.v17_data_builder.features import MIN_HISTORY_BARS
from services.v17_data_builder.ohlcv_cache import OHLCVCacheReader, OHLCVReadResult
from services.v17_shadow.ledger import LedgerAppendResult, V17ShadowLedger
from services.v17_signal_engine.event_aggregator import build_candidate_signal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"
PRODUCTION_TARGET_STREAM = "alpha.target"
REQUIRED_V17_STREAM_PREFIXES = {
    "features_1h": "v17.",
    "signals": "v17.",
    "shadow_ledger": "v17.",
}


class SignalWriter(Protocol):
    def write(self, stream: str, payload: Mapping[str, Any]) -> Any:
        ...


@dataclass(frozen=True)
class ShadowEventResult:
    symbol: str
    side: V17Side
    status: str
    reason: str
    event: V17SignalEvent | None = None
    signal_stream: str = ""
    ledger_results: tuple[LedgerAppendResult, ...] = field(default_factory=tuple)

    @property
    def duplicate(self) -> bool:
        return any(result.duplicate for result in self.ledger_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "status": self.status,
            "reason": self.reason,
            "event": self.event.to_dict() if self.event else None,
            "signal_stream": self.signal_stream,
            "ledger_results": [result.row for result in self.ledger_results],
        }


@dataclass(frozen=True)
class ShadowRunResult:
    status: str
    reason: str
    events: tuple[ShadowEventResult, ...] = field(default_factory=tuple)

    @property
    def processed_count(self) -> int:
        return len(self.events)

    @property
    def valid_count(self) -> int:
        return sum(1 for event in self.events if event.status == "valid")

    @property
    def invalid_count(self) -> int:
        return sum(1 for event in self.events if event.status == "invalid")

    @property
    def blocked_count(self) -> int:
        return sum(1 for event in self.events if event.status == "blocked")

    @property
    def duplicate_count(self) -> int:
        return sum(1 for event in self.events if event.status == "duplicate")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "processed_count": self.processed_count,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "blocked_count": self.blocked_count,
            "duplicate_count": self.duplicate_count,
            "events": [event.to_dict() for event in self.events],
        }


class V17ShadowRunner:
    def __init__(
        self,
        *,
        config: Mapping[str, Any] | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        ohlcv_reader: Any | None = None,
        ledger: Any | None = None,
        signal_writer: SignalWriter | Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.config = dict(config) if config is not None else load_config(self.config_path)
        validate_shadow_config(self.config)
        self.ohlcv_reader = ohlcv_reader or _reader_from_config(self.config)
        self.ledger = ledger or V17ShadowLedger(_ledger_path(self.config))
        self.signal_writer = signal_writer

    def run(
        self,
        *,
        symbols: Sequence[str],
        as_of: datetime | str | None = None,
        sides: Mapping[str, V17Side] | V17Side | None = None,
    ) -> ShadowRunResult:
        normalized_symbols = tuple(_normalize_symbols(symbols))
        if not normalized_symbols:
            return ShadowRunResult(status="blocked", reason="blocked_by_config:no_symbols")

        if not _config_enabled(self.config):
            events = tuple(
                ShadowEventResult(
                    symbol=symbol,
                    side=_side_for_symbol(symbol, sides),
                    status="blocked",
                    reason="blocked_by_config:v17_shadow_disabled",
                )
                for symbol in normalized_symbols
            )
            return ShadowRunResult(
                status="blocked",
                reason="blocked_by_config:v17_shadow_disabled",
                events=events,
            )

        as_of_utc = _coerce_as_of(as_of)
        candidate_events: list[V17SignalEvent] = []
        event_results: list[ShadowEventResult] = []
        for symbol in normalized_symbols:
            side = _side_for_symbol(symbol, sides)
            read_result = self._read_symbol(symbol, as_of=as_of_utc)
            signal_result = build_candidate_signal(
                read_result,
                side=side,
                event_gross_cap=_event_gross_cap(self.config),
            )
            candidate_events.append(signal_result.to_event())

        for event in _aggregate_candidate_events(candidate_events, event_gross_cap=_event_gross_cap(self.config)):
            self._write_signal(event)
            ledger_results = tuple(self.ledger.append_event(event))
            event_status = _event_status(event, ledger_results)
            event_results.append(
                ShadowEventResult(
                    symbol=",".join(leg.symbol for leg in event.legs),
                    side=event.legs[0].side if len(event.legs) == 1 else "FLAT",
                    status=event_status,
                    reason=_event_reason(event, ledger_results),
                    event=event,
                    signal_stream=_signals_stream(self.config),
                    ledger_results=ledger_results,
                )
            )

        status = "valid" if any(event.status == "valid" for event in event_results) else event_results[0].status
        return ShadowRunResult(status=status, reason="shadow_run_complete", events=tuple(event_results))

    def _read_symbol(self, symbol: str, *, as_of: datetime) -> OHLCVReadResult:
        if hasattr(self.ohlcv_reader, "read_symbol"):
            return cast(OHLCVReadResult, self.ohlcv_reader.read_symbol(
                symbol,
                as_of=as_of,
                min_history=MIN_HISTORY_BARS,
                raise_on_invalid=False,
            ))
        if callable(self.ohlcv_reader):
            reader = cast(Callable[..., OHLCVReadResult], self.ohlcv_reader)
            return reader(symbol, as_of=as_of, min_history=MIN_HISTORY_BARS)
        raise TypeError("ohlcv_reader must provide read_symbol or be callable")

    def _write_signal(self, event: V17SignalEvent) -> None:
        stream = _signals_stream(self.config)
        _assert_v17_stream("signals", stream)
        payload = event.to_dict()
        if self.signal_writer is None:
            return
        if hasattr(self.signal_writer, "write"):
            writer = cast(SignalWriter, self.signal_writer)
            writer.write(stream, payload)
            return
        write_signal = cast(Callable[[str, Mapping[str, Any]], Any], self.signal_writer)
        write_signal(stream, payload)


def run_shadow_once(
    *,
    symbols: Sequence[str],
    as_of: datetime | str | None = None,
    sides: Mapping[str, V17Side] | V17Side | None = None,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    ohlcv_reader: Any | None = None,
    ledger: Any | None = None,
    signal_writer: SignalWriter | Callable[[str, Mapping[str, Any]], Any] | None = None,
) -> ShadowRunResult:
    runner = V17ShadowRunner(
        config=config,
        config_path=config_path,
        ohlcv_reader=ohlcv_reader,
        ledger=ledger,
        signal_writer=signal_writer,
    )
    return runner.run(symbols=symbols, as_of=as_of, sides=sides)


def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with Path(config_path).open() as config_file:
        return json.load(config_file)


def validate_shadow_config(config: Mapping[str, Any]) -> None:
    streams = _streams(config)
    for name, prefix in REQUIRED_V17_STREAM_PREFIXES.items():
        _assert_v17_stream(name, str(streams.get(name, "")), prefix=prefix)
    target_stream = str(streams.get("target", ""))
    if target_stream == PRODUCTION_TARGET_STREAM:
        raise ValueError("V17 target stream must not be production alpha.target")
    if target_stream and target_stream != "alpha.target.v17_shadow":
        raise ValueError("V17 target stream must remain alpha.target.v17_shadow")
    if config.get("real_money_execution_allowed") is not False:
        raise ValueError("V17 shadow runner requires real_money_execution_allowed=false")
    if config.get("mode") != "paper_shadow":
        raise ValueError("V17 shadow runner requires mode=paper_shadow")
    if config.get("dry_run") is not True:
        raise ValueError("V17 shadow runner requires dry_run=true")


def _reader_from_config(config: Mapping[str, Any]) -> OHLCVCacheReader:
    data_path = config.get("ohlcv_cache_path") or config.get("data_path")
    if not data_path:
        raise ValueError("ohlcv_reader or config ohlcv_cache_path is required")
    return OHLCVCacheReader.from_csv(
        PROJECT_ROOT / data_path if not Path(str(data_path)).is_absolute() else data_path,
        freshness_tolerance_seconds=_freshness_tolerance(config),
    )


def _ledger_path(config: Mapping[str, Any]) -> Path:
    ledger_config = config.get("ledger", {})
    if not isinstance(ledger_config, Mapping):
        raise ValueError("ledger config must be a mapping")
    path = Path(str(ledger_config.get("path", "data/v17_shadow/ledger.csv")))
    return path if path.is_absolute() else PROJECT_ROOT / path


def _streams(config: Mapping[str, Any]) -> Mapping[str, Any]:
    streams = config.get("streams", {})
    if not isinstance(streams, Mapping):
        raise ValueError("streams config must be a mapping")
    return streams


def _aggregate_candidate_events(
    events: Sequence[V17SignalEvent],
    *,
    event_gross_cap: float,
) -> tuple[V17SignalEvent, ...]:
    groups: dict[tuple[str, str, str], list[V17SignalEvent]] = {}
    for event in events:
        key = (event.entry_time_utc, event.bar_close_time_utc, event.strategy_version)
        groups.setdefault(key, []).append(event)

    aggregated: list[V17SignalEvent] = []
    for key in sorted(groups):
        group = groups[key]
        if len(group) == 1:
            aggregated.append(group[0])
            continue
        aggregated.append(_merge_event_group(group, event_gross_cap=event_gross_cap))
    return tuple(aggregated)


def _merge_event_group(events: Sequence[V17SignalEvent], *, event_gross_cap: float) -> V17SignalEvent:
    first = events[0]
    valid_legs = [leg for event in events for leg in event.legs if leg.status == "valid"]
    invalid_legs = [leg for event in events for leg in event.legs if leg.status != "valid"]
    if valid_legs:
        raw_gross = sum(abs(leg.final_leg_weight) for leg in valid_legs)
        multiplier = min(1.0, event_gross_cap / raw_gross) if raw_gross > 0 else 1.0
        merged_legs = []
        event_id = _aggregate_event_id(first.entry_time_utc, first.bar_close_time_utc, first.strategy_version, valid_legs)
        for index, leg in enumerate(valid_legs):
            final_weight = ComparableWeight(round(leg.final_leg_weight * multiplier, 12))
            raw_weight = ComparableWeight(abs(leg.final_leg_weight))
            merged_legs.append(
                leg.model_copy(
                    update={
                        "event_id": event_id,
                        "leg_id": f"{event_id}:leg:{index}",
                        "raw_leg_weight": raw_weight,
                        "final_leg_weight": final_weight,
                    }
                )
            )
        return V17SignalEvent(
            event_id=event_id,
            entry_time_utc=first.entry_time_utc,
            bar_close_time_utc=first.bar_close_time_utc,
            event_gross_cap=event_gross_cap,
            legs=merged_legs,
            status="valid",
            reason="shadow_event_aggregated" if multiplier == 1.0 else "shadow_event_aggregated:cap_scaled",
            strategy_version=first.strategy_version,
        )

    event_id = _aggregate_event_id(first.entry_time_utc, first.bar_close_time_utc, first.strategy_version, invalid_legs)
    merged_invalid = [
        leg.model_copy(update={"event_id": event_id, "leg_id": f"{event_id}:leg:{index}"})
        for index, leg in enumerate(invalid_legs)
    ]
    reason = ";".join(dict.fromkeys(event.reason for event in events if event.reason)) or "no_valid_legs"
    return V17SignalEvent(
        event_id=event_id,
        entry_time_utc=first.entry_time_utc,
        bar_close_time_utc=first.bar_close_time_utc,
        event_gross_cap=event_gross_cap,
        legs=merged_invalid,
        status="invalid",
        reason=reason,
        strategy_version=first.strategy_version,
    )


def _aggregate_event_id(entry_time_utc: str, bar_close_time_utc: str, strategy_version: str, legs: Sequence[Any]) -> str:
    symbols = "+".join(sorted(str(leg.symbol).upper() for leg in legs)) or "NONE"
    return f"v17-shadow-event:{strategy_version}:{bar_close_time_utc}:{entry_time_utc}:{symbols}"


def _signals_stream(config: Mapping[str, Any]) -> str:
    return str(_streams(config).get("signals", ""))


def _assert_v17_stream(name: str, stream: str, *, prefix: str = "v17.") -> None:
    if stream == PRODUCTION_TARGET_STREAM:
        raise ValueError(f"{name} stream must not be production alpha.target")
    if not stream.startswith(prefix):
        raise ValueError(f"{name} stream must be V17-specific")


def _config_enabled(config: Mapping[str, Any]) -> bool:
    return config.get("enabled") is True


def _freshness_tolerance(config: Mapping[str, Any]) -> int | None:
    freshness = config.get("freshness", {})
    if not isinstance(freshness, Mapping):
        return None
    value = freshness.get("max_completed_bar_lag_seconds")
    return None if value is None else int(value)


def _event_gross_cap(config: Mapping[str, Any]) -> float:
    caps = config.get("caps", {})
    if not isinstance(caps, Mapping):
        return 0.06
    return float(caps.get("max_event_gross", 0.06))


def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for symbol in symbols:
        value = str(symbol).strip().upper()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _side_for_symbol(symbol: str, sides: Mapping[str, V17Side] | V17Side | None) -> V17Side:
    if sides is None:
        return "LONG"
    if isinstance(sides, str):
        return sides
    return sides.get(symbol, sides.get(symbol.upper(), "LONG"))


def _coerce_as_of(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def _event_status(event: V17SignalEvent, ledger_results: Sequence[LedgerAppendResult]) -> str:
    if ledger_results and all(result.duplicate for result in ledger_results):
        return "duplicate"
    return event.status


def _event_reason(event: V17SignalEvent, ledger_results: Sequence[LedgerAppendResult]) -> str:
    if ledger_results and all(result.duplicate for result in ledger_results):
        return "duplicate_event"
    return event.reason
