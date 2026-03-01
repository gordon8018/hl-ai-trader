from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from services.openclaw_ops.redis_client import RedisClient


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_env(source: str) -> Dict[str, Any]:
    ts = utc_now_iso()
    cycle_id = ts.replace("-", "").replace(":", "").replace("T", "T")
    cycle_id = cycle_id[:13] + "00Z"
    return {
        "event_id": ts,
        "ts": ts,
        "source": source,
        "cycle_id": cycle_id,
        "retry_count": 0,
        "schema_version": "1.0",
    }


def write_audit(bus: RedisClient, event: str, data: Dict[str, Any], *, source: str) -> str:
    payload = {"env": build_env(source), "event": event, "data": data}
    return bus.xadd_stream("audit.logs", payload)
