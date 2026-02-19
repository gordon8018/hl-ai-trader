# shared/bus/dlq.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal

from shared.bus.guard import require_env
from shared.schemas import Envelope

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

def _normalize_env(env: Any) -> Dict[str, Any]:
    if isinstance(env, Envelope):
        return env.model_dump()
    if isinstance(env, dict):
        return env
    raise TypeError("env must be Envelope or dict")

def retry_or_dlq(
    bus,
    stream: str,
    payload: Dict[str, Any],
    env: Any,
    policy: RetryPolicy,
    *,
    audit_stream: str = "audit.logs",
    reason: Optional[str] = None,
) -> Literal["retry", "dlq"]:
    env_dict = _normalize_env(env)
    rc = next_retry_count(payload)
    if rc > policy.max_retries:
        bus.xadd_json(f"dlq.{stream}", payload)
        bus.xadd_json(
            audit_stream,
            require_env(
                {
                    "env": env_dict,
                    "event": "dlq",
                    "stream": stream,
                    "retry_count": rc,
                    "reason": reason or "max_retries_exceeded",
                }
            ),
        )
        return "dlq"

    bus.xadd_json(stream, with_retry_count(payload, rc))
    bus.xadd_json(
        audit_stream,
        require_env(
            {
                "env": env_dict,
                "event": "retry",
                "stream": stream,
                "retry_count": rc,
                "reason": reason or "error",
            }
        ),
    )
    return "retry"
