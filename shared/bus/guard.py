# shared/bus/guard.py
from __future__ import annotations
from typing import Dict, Any
from shared.schemas import Envelope

def require_env(payload: Dict[str, Any]) -> Dict[str, Any]:
    if "env" not in payload:
        raise ValueError("Missing env in payload")
    # validate + normalize
    env = Envelope(**payload["env"])
    payload["env"] = env.model_dump()
    return payload
