# shared/bus/dlq.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class RetryPolicy:
    max_retries: int = 5

def next_retry_count(payload: Dict[str, Any]) -> int:
    env = payload.get("env", {}) if isinstance(payload, dict) else {}
    rc = int(env.get("retry_count", 0))
    return rc + 1

def with_retry_count(payload: Dict[str, Any], retry_count: int) -> Dict[str, Any]:
    payload = dict(payload)
    env = dict(payload.get("env", {}))
    env["retry_count"] = retry_count
    payload["env"] = env
    return payload
