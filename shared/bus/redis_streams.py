from __future__ import annotations
import json
import redis
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime
from redis.exceptions import ResponseError
import uuid

class RedisStreams:
    def __init__(self, redis_url: str):
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)

    def xadd_json(self, stream: str, obj: Dict[str, Any], maxlen: Optional[int] = 20000) -> str:
        # store payload as single field to avoid redis field limitations
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return str(self.r.xadd(stream, {"p": payload}, maxlen=maxlen, approximate=True))

    def xreadgroup_json(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 50,
        block_ms: int = 5000,
        recover_pending: bool = True,
        min_idle_ms: int = 30000,
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        # returns list of (stream, id, payload_json)
        try:
            if recover_pending:
                reclaimed = self._xautoclaim_json(stream, group, consumer, count=count, min_idle_ms=min_idle_ms)
                if reclaimed:
                    return reclaimed

            resp = self.r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        except ResponseError as e:
            if "NOGROUP" in str(e):
                self.r.xgroup_create(stream, group, id="0-0", mkstream=True)
                if recover_pending:
                    reclaimed = self._xautoclaim_json(stream, group, consumer, count=count, min_idle_ms=min_idle_ms)
                    if reclaimed:
                        return reclaimed
                resp = self.r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
            else:
                raise

        out: List[Tuple[str, str, Dict[str, Any]]] = []
        for _, msgs in resp: # type: ignore
            for msg_id, fields in msgs:
                if "p" not in fields:
                    self._audit_missing_payload(stream, msg_id, fields)
                    raise ValueError(f"missing payload field 'p' in stream={stream} id={msg_id}")
                out.append((stream, msg_id, json.loads(fields["p"])))
        return out

    def _xautoclaim_json(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
        min_idle_ms: int,
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        Try to reclaim pending entries (stuck in PEL) before reading new ones.
        If redis version/client does not support XAUTOCLAIM, safely fallback.
        """
        try:
            # redis-py returns (next_start, [(id, fields), ...], [deleted_ids]) in newer versions
            resp = self.r.xautoclaim(stream, group, consumer, min_idle_ms, "0-0", count=count)  # type: ignore[attr-defined]
        except Exception:
            return []

        if not isinstance(resp, (list, tuple)) or len(resp) < 2:
            return []

        msgs = resp[1] or []
        out: List[Tuple[str, str, Dict[str, Any]]] = []
        for msg_id, fields in msgs:
            if "p" not in fields:
                self._audit_missing_payload(stream, msg_id, fields)
                raise ValueError(f"missing payload field 'p' in stream={stream} id={msg_id}")
            out.append((stream, msg_id, json.loads(fields["p"])))
        return out

    def xack(self, stream: str, group: str, msg_id: str) -> int:
        return self.r.xack(stream, group, msg_id) # type: ignore

    def set_json(self, key: str, obj: Dict[str, Any], ex: Optional[int] = None) -> bool:
        return bool(self.r.set(key, json.dumps(obj, ensure_ascii=False), ex=ex))

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        v = self.r.get(key)
        if not v:
            return None
        return json.loads(v) # type: ignore

    def acquire_lock(self, key: str, ttl_s: int = 10) -> bool:
        return bool(self.r.set(key, "1", nx=True, ex=ttl_s))

    def release_lock(self, key: str) -> None:
        self.r.delete(key)

    def _audit_missing_payload(self, stream: str, msg_id: str, fields: Dict[str, Any]) -> None:
        env = {
            "event_id": str(uuid.uuid4()),
            "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "source": "redis_streams",
            "cycle_id": "UNKNOWN",
            "schema_version": "1.0",
            "retry_count": 0,
        }
        data = {
            "reason": "missing payload field 'p'",
            "stream": stream,
            "msg_id": msg_id,
            "fields": fields,
        }
        self.xadd_json("audit.logs", {"env": env, "data": data, "event": "stream_payload_missing"})