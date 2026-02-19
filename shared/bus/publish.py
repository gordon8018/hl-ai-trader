# shared/bus/publish.py
from __future__ import annotations

from typing import Any, Dict, Optional
from shared.schemas import Envelope
from shared.time_utils import current_cycle_id

def _normalize_env(env: Any, *, default_source: str) -> Dict[str, Any]:
    """
    Normalize and validate env:
    - If env is dict -> validate via Envelope
    - If env is Envelope -> use it
    - If env is missing -> create one with current_cycle_id (ONLY for non-trading heartbeat/audit)
    """
    if env is None:
        # Only safe default is "now" for non-critical events.
        e = Envelope(source=default_source, cycle_id=current_cycle_id())
        return e.model_dump()

    if isinstance(env, Envelope):
        return env.model_dump()

    if isinstance(env, dict):
        # Ensure source exists
        if "source" not in env or not env["source"]:
            env = dict(env)
            env["source"] = default_source
        e = Envelope(**env)  # validates cycle_id format
        return e.model_dump()

    raise TypeError(f"Unsupported env type: {type(env)}")


def publish(bus, stream: str, *, env: Any, data: Any = None, event: Optional[str] = None, **extra) -> str:
    """
    Publish a message to Redis Stream with strict Envelope validation.

    Usage:
        publish(bus, "alpha.target", env=env, data=tp.model_dump())
        publish(bus, "audit.logs", env=env, event="error", err="...", raw=payload)

    Rules:
    - env MUST pass Envelope validation (cycle_id required & formatted).
    - data should be JSON-serializable (dict).
    - Any extra kwargs are merged at top-level.

    Returns: redis stream message id
    """
    env_dict = _normalize_env(env, default_source=getattr(env, "source", None) or extra.get("source") or "unknown")

    payload: Dict[str, Any] = {"env": env_dict}
    if event is not None:
        payload["event"] = event
    if data is not None:
        payload["data"] = data
    payload.update(extra)

    # Optional: enforce that data is dict-like if provided
    # if data is not None and not isinstance(data, dict):
    #     raise TypeError("data must be a dict (use .model_dump())")

    return bus.xadd_json(stream, payload)
