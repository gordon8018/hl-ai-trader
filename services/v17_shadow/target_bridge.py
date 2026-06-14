from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, cast

from shared.schemas import TargetPortfolio, TargetWeight
from shared.v17.schemas import V17LedgerRow, V17SignalEvent, V17SignalLeg
from services.v17_shadow.ledger import read_rows
from services.v17_shadow.runner import DEFAULT_CONFIG_PATH, PRODUCTION_TARGET_STREAM, load_config

V17_TARGET_STREAM = "alpha.target.v17_shadow"
DEFAULT_DECISION_HORIZON = "1h"


class TargetWriter(Protocol):
    def write(self, stream: str, payload: Mapping[str, Any]) -> Any:
        ...


@dataclass(frozen=True)
class BridgeResult:
    status: str
    reason: str
    stream: str
    target: TargetPortfolio | None = None
    write_result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "stream": self.stream,
            "target": self.target.model_dump(mode="json") if self.target else None,
            "write_result": self.write_result,
        }


class V17TargetBridge:
    def __init__(
        self,
        *,
        config: Mapping[str, Any] | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        writer: TargetWriter | Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.config = dict(config) if config is not None else load_config(self.config_path)
        self.stream = _target_stream(self.config)
        validate_target_bridge_config(self.config)
        self.writer = writer

    def bridge_event(self, event: V17SignalEvent | Mapping[str, Any]) -> BridgeResult:
        parsed = _coerce_event(event)
        if parsed.status != "valid":
            return BridgeResult(status="blocked", reason=f"event_status:{parsed.status}", stream=self.stream)

        target = event_to_target_portfolio(parsed, config=self.config)
        if not target.targets:
            return BridgeResult(status="blocked", reason="no_valid_target_legs", stream=self.stream, target=target)

        write_result = self._write(target)
        return BridgeResult(
            status="valid",
            reason="target_written" if self.writer is not None else "target_built",
            stream=self.stream,
            target=target,
            write_result=write_result,
        )

    def bridge_rows(self, rows: Sequence[V17LedgerRow | Mapping[str, Any]]) -> BridgeResult:
        event = event_from_ledger_rows(rows, config=self.config)
        if event is None:
            return BridgeResult(status="blocked", reason="no_valid_event_rows", stream=self.stream)
        return self.bridge_event(event)

    def bridge_ledger(self, path: str | Path) -> list[BridgeResult]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in read_rows(path):
            if row.get("status") == "valid":
                grouped.setdefault(str(row.get("event_id", "")), []).append(row)
        return [self.bridge_rows(grouped[event_id]) for event_id in sorted(grouped)]

    def _write(self, target: TargetPortfolio) -> Any:
        payload = target.model_dump(mode="json")
        if self.writer is None:
            return None
        if hasattr(self.writer, "write"):
            writer = cast(TargetWriter, self.writer)
            return writer.write(self.stream, payload)
        write_target = cast(Callable[[str, Mapping[str, Any]], Any], self.writer)
        return write_target(self.stream, payload)


def bridge_event(
    event: V17SignalEvent | Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    writer: TargetWriter | Callable[[str, Mapping[str, Any]], Any] | None = None,
) -> BridgeResult:
    return V17TargetBridge(config=config, config_path=config_path, writer=writer).bridge_event(event)


def bridge_rows(
    rows: Sequence[V17LedgerRow | Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    writer: TargetWriter | Callable[[str, Mapping[str, Any]], Any] | None = None,
) -> BridgeResult:
    return V17TargetBridge(config=config, config_path=config_path, writer=writer).bridge_rows(rows)


def bridge_ledger(
    path: str | Path,
    *,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    writer: TargetWriter | Callable[[str, Mapping[str, Any]], Any] | None = None,
) -> list[BridgeResult]:
    return V17TargetBridge(config=config, config_path=config_path, writer=writer).bridge_ledger(path)


def event_to_target_portfolio(event: V17SignalEvent, *, config: Mapping[str, Any]) -> TargetPortfolio:
    valid_legs = tuple(leg for leg in event.legs if leg.status == "valid")
    capped_legs = _cap_legs(
        valid_legs,
        event_gross_cap=min(event.event_gross_cap, _event_gross_cap(config)),
        max_symbol_gross=_max_symbol_gross(config),
    )
    universe = [leg.symbol for leg in capped_legs]
    targets = [TargetWeight(symbol=leg.symbol, weight=leg.final_leg_weight) for leg in capped_legs]
    gross_weight = sum(abs(target.weight) for target in targets)
    caps = _caps(config)
    return TargetPortfolio(
        asof_minute=event.entry_time_utc,
        universe=universe,
        targets=targets,
        cash_weight=max(0.0, 1.0 - gross_weight),
        confidence=1.0 if targets else 0.0,
        rationale=event.reason,
        model={"name": "v17", "version": event.strategy_version, "mode": "paper_shadow"},
        constraints_hint={
            "event_gross_cap": min(event.event_gross_cap, _event_gross_cap(config)),
            "max_gross": float(caps.get("max_gross", 0.0)),
            "max_net": float(caps.get("max_net", 0.0)),
            "max_symbol_gross": float(caps.get("max_symbol_gross", 0.0)),
        },
        decision_horizon=DEFAULT_DECISION_HORIZON,
        major_cycle_id=event.event_id,
        decision_action="REBALANCE" if targets else "HOLD",
        evidence={
            "source": "v17_shadow_target_bridge",
            "mode": "paper_shadow",
            "event": event.to_dict(),
        },
    )


def event_from_ledger_rows(
    rows: Sequence[V17LedgerRow | Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
) -> V17SignalEvent | None:
    valid_rows = [_normalize_row(row) for row in rows if _row_status(row) == "valid"]
    if not valid_rows:
        return None

    first = valid_rows[0]
    event_id = str(first["event_id"])
    if any(str(row["event_id"]) != event_id for row in valid_rows):
        raise ValueError("ledger rows must belong to one event_id")

    legs = [
        V17SignalLeg(
            event_id=event_id,
            leg_id=str(row["leg_id"]),
            symbol=str(row["symbol"]).upper(),
            side=cast(Any, row["side"]),
            quality_score=cast(float | None, row.get("quality_score")),
            quality_label=cast(Any, row["quality_label"]),
            raw_leg_weight=float(row["raw_leg_weight"]),
            final_leg_weight=float(row["final_leg_weight"]),
            status="valid",
            reason=str(row.get("reason", "")),
        )
        for row in valid_rows
    ]
    return V17SignalEvent(
        event_id=event_id,
        entry_time_utc=str(first["entry_time_utc"]),
        bar_close_time_utc=str(first["bar_close_time_utc"]),
        event_gross_cap=min(float(first["event_gross_cap"]), _event_gross_cap(config)),
        legs=legs,
        status="valid",
        reason="v17_ledger_event_approved",
        strategy_version=str(first.get("strategy_version") or "v17"),
    )


def validate_target_bridge_config(config: Mapping[str, Any]) -> None:
    if config.get("mode") != "paper_shadow":
        raise ValueError("V17 target bridge requires mode=paper_shadow")
    if config.get("dry_run") is not True:
        raise ValueError("V17 target bridge requires dry_run=true")
    if config.get("real_money_execution_allowed") is not False:
        raise ValueError("V17 target bridge requires real_money_execution_allowed=false")
    stream = _target_stream(config)
    if stream == PRODUCTION_TARGET_STREAM:
        raise ValueError("V17 target bridge must not write production alpha.target")
    if stream != V17_TARGET_STREAM:
        raise ValueError("V17 target bridge stream must remain alpha.target.v17_shadow")


def _target_stream(config: Mapping[str, Any]) -> str:
    streams = config.get("streams", {})
    if not isinstance(streams, Mapping):
        raise ValueError("streams config must be a mapping")
    return str(streams.get("target", ""))


def _caps(config: Mapping[str, Any]) -> Mapping[str, Any]:
    caps = config.get("caps", {})
    if not isinstance(caps, Mapping):
        return {}
    return caps


def _event_gross_cap(config: Mapping[str, Any]) -> float:
    return float(_caps(config).get("max_event_gross", 0.06))


def _max_symbol_gross(config: Mapping[str, Any]) -> float | None:
    value = _caps(config).get("max_symbol_gross")
    return None if value is None else float(value)


def _cap_legs(
    legs: Sequence[V17SignalLeg],
    *,
    event_gross_cap: float,
    max_symbol_gross: float | None,
) -> tuple[V17SignalLeg, ...]:
    symbol_capped = []
    for leg in legs:
        weight = float(leg.final_leg_weight)
        if max_symbol_gross is not None and abs(weight) > max_symbol_gross:
            weight = max_symbol_gross if weight > 0 else -max_symbol_gross
        symbol_capped.append(leg.model_copy(update={"final_leg_weight": round(weight, 12)}))

    gross = sum(abs(leg.final_leg_weight) for leg in symbol_capped)
    multiplier = min(1.0, event_gross_cap / gross) if gross > 0 else 1.0
    return tuple(
        leg.model_copy(update={"final_leg_weight": round(float(leg.final_leg_weight) * multiplier, 12)})
        for leg in symbol_capped
    )

def _coerce_event(event: V17SignalEvent | Mapping[str, Any]) -> V17SignalEvent:
    if isinstance(event, V17SignalEvent):
        return event
    return V17SignalEvent.from_dict(dict(event))


def _normalize_row(row: V17LedgerRow | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, V17LedgerRow):
        return row.to_dict()
    return dict(row)


def _row_status(row: V17LedgerRow | Mapping[str, Any]) -> str:
    if isinstance(row, V17LedgerRow):
        return row.status
    return str(row.get("status", ""))


def target_to_json(target: TargetPortfolio) -> str:
    return json.dumps(target.model_dump(mode="json"), sort_keys=True)
