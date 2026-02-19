# shared/time_utils.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Union

ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%MZ",
)

def _parse_asof(value: Union[str, datetime]) -> datetime:
    """
    Parse asof_minute into a timezone-aware UTC datetime.
    Accepts:
        2026-02-18T16:00:00Z
        2026-02-18T16:00Z
        2026-02-18T16:00:00.123Z
        datetime (naive or aware)
    """
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            # assume UTC if naive
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if not isinstance(value, str):
        raise TypeError(f"Invalid asof_minute type: {type(value)}")

    s = value.strip()

    for fmt in ISO_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise ValueError(f"Invalid asof_minute format: {value}")


def floor_to_minute(dt: datetime) -> datetime:
    """
    Align timestamp to minute boundary.
    16:00:59.999 -> 16:00:00
    """
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def cycle_id_from_asof_minute(asof_minute: Union[str, datetime]) -> str:
    """
    Convert any accepted asof_minute to canonical cycle_id.

    Example:
        2026-02-18T16:00:00Z  -> 20260218T1600Z
        2026-02-18T16:00:59Z  -> 20260218T1600Z
    """
    dt = _parse_asof(asof_minute)
    dt = floor_to_minute(dt)
    return dt.strftime("%Y%m%dT%H%MZ")


def current_cycle_id() -> str:
    """
    Current UTC minute cycle id.
    Used only by guard/audit, NOT trading logic.
    """
    return cycle_id_from_asof_minute(datetime.now(timezone.utc))
