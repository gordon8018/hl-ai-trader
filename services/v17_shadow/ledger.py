from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from shared.v17.schemas import V17LedgerRow, V17SignalEvent, V17SignalLeg

LEDGER_FIELDNAMES = (
    "recorded_at_utc",
    "event_id",
    "leg_id",
    "entry_time_utc",
    "bar_close_time_utc",
    "symbol",
    "side",
    "quality_score",
    "quality_label",
    "event_gross_cap",
    "raw_leg_weight",
    "final_leg_weight",
    "status",
    "reason",
    "strategy_version",
)

NUMERIC_FIELDS = {"quality_score", "event_gross_cap", "raw_leg_weight", "final_leg_weight"}
LEDGER_STATUSES = {"valid", "invalid", "blocked", "duplicate"}


@dataclass(frozen=True)
class LedgerAppendResult:
    row: dict[str, Any]
    status: str
    duplicate: bool = False


class V17ShadowLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append_event(self, event: V17SignalEvent) -> list[LedgerAppendResult]:
        return [self.append_leg(event, leg) for leg in event.legs]

    def append_leg(self, event: V17SignalEvent, leg: V17SignalLeg) -> LedgerAppendResult:
        row = V17LedgerRow(
            event_id=event.event_id,
            leg_id=leg.leg_id,
            entry_time_utc=event.entry_time_utc,
            bar_close_time_utc=event.bar_close_time_utc,
            symbol=leg.symbol,
            side=leg.side,
            quality_score=leg.quality_score,
            quality_label=leg.quality_label,
            event_gross_cap=event.event_gross_cap,
            raw_leg_weight=leg.raw_leg_weight,
            final_leg_weight=leg.final_leg_weight,
            status=leg.status if event.status == "valid" else event.status,
            reason=leg.reason or event.reason,
            strategy_version=event.strategy_version,
        )
        return self.append_row(row)

    def append_row(self, row: V17LedgerRow | Mapping[str, Any]) -> LedgerAppendResult:
        payload = _normalize_row(row)
        key = _ledger_key(payload)
        existing_keys = self._active_keys()
        if key in existing_keys:
            duplicate_row = _duplicate_row(payload)
            self._append_csv_row(duplicate_row)
            return LedgerAppendResult(row=duplicate_row, status="duplicate", duplicate=True)

        self._append_csv_row(payload)
        return LedgerAppendResult(row=payload, status=str(payload["status"]), duplicate=False)

    def read_rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", newline="") as file_obj:
            reader = csv.DictReader(file_obj)
            return [_deserialize_row(row) for row in reader]

    def _active_keys(self) -> set[tuple[str, str]]:
        return {
            _ledger_key(row)
            for row in self.read_rows()
            if row.get("status") != "duplicate"
        }

    def _append_csv_row(self, row: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="") as file_obj:
            writer: csv.DictWriter[str] = csv.DictWriter(
                file_obj,
                fieldnames=list(LEDGER_FIELDNAMES),
                extrasaction="ignore",
            )
            if write_header:
                writer.writeheader()
            writer.writerow(_serialize_row(row))


def append_event(path: str | Path, event: V17SignalEvent) -> list[LedgerAppendResult]:
    return V17ShadowLedger(path).append_event(event)


def append_row(path: str | Path, row: V17LedgerRow | Mapping[str, Any]) -> LedgerAppendResult:
    return V17ShadowLedger(path).append_row(row)


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    return V17ShadowLedger(path).read_rows()


def _normalize_row(row: V17LedgerRow | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, V17LedgerRow):
        payload = row.to_dict()
    else:
        payload = dict(row)

    status = str(payload.get("status", ""))
    if status not in LEDGER_STATUSES - {"duplicate"}:
        raise ValueError(f"unsupported ledger status: {status}")

    normalized = {field: payload.get(field, "") for field in LEDGER_FIELDNAMES}
    normalized["recorded_at_utc"] = payload.get("recorded_at_utc") or _utc_now_iso()
    return normalized


def _duplicate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    duplicate = dict(row)
    duplicate["recorded_at_utc"] = _utc_now_iso()
    duplicate["status"] = "duplicate"
    duplicate["raw_leg_weight"] = 0.0
    duplicate["final_leg_weight"] = 0.0
    reason = str(row.get("reason") or "")
    duplicate["reason"] = "duplicate_event_leg" if not reason else f"duplicate_event_leg:{reason}"
    return duplicate


def _ledger_key(row: Mapping[str, Any]) -> tuple[str, str]:
    event_id = str(row.get("event_id") or "")
    leg_id = str(row.get("leg_id") or "")
    if not event_id or not leg_id:
        raise ValueError("ledger row requires event_id and leg_id")
    return event_id, leg_id


def _serialize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for field in LEDGER_FIELDNAMES:
        value = row.get(field, "")
        if value is None:
            serialized[field] = ""
        elif isinstance(value, (dict, list)):
            serialized[field] = json.dumps(value, sort_keys=True)
        else:
            serialized[field] = value
    return serialized


def _deserialize_row(row: Mapping[str, str | None]) -> dict[str, Any]:
    deserialized: dict[str, Any] = {}
    for field in LEDGER_FIELDNAMES:
        value = row.get(field, "")
        if field in NUMERIC_FIELDS:
            deserialized[field] = None if value in (None, "") else float(value)
        else:
            deserialized[field] = "" if value is None else value
    return deserialized


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
