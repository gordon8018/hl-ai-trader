from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import redis


class RedisClient:
    def __init__(self, redis_url: str):
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)

    def xadd_stream(self, stream: str, payload: Dict[str, Any], maxlen: int = 20000) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return str(self.r.xadd(stream, {"p": raw}, maxlen=maxlen, approximate=True))

    def read_last(self, stream: str, count: int) -> List[Dict[str, Any]]:
        rows = self.r.xrevrange(stream, count=count)  # type: ignore[arg-type]
        out: List[Dict[str, Any]] = []
        for _, fields in rows:
            raw = fields.get("p")
            if isinstance(raw, str):
                try:
                    out.append(json.loads(raw))
                except Exception:
                    out.append({"raw": raw})
        return out

    def set_if_absent(self, key: str, value: Dict[str, Any], ttl_s: int) -> bool:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return bool(self.r.set(key, raw, nx=True, ex=ttl_s))

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self.r.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
