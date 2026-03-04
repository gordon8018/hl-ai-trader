from __future__ import annotations
import json
import redis
from typing import Any, Dict, Optional, Tuple, List, cast
from datetime import datetime
from redis.exceptions import ResponseError
import uuid

# Global connection pool cache to avoid creating duplicate pools
_connection_pools: Dict[str, redis.ConnectionPool] = {}
_connection_pool_refs: Dict[str, int] = {}


def _pool_key(redis_url: str, max_connections: int) -> str:
    return f"{redis_url}:{max_connections}"


def _get_connection_pool(
    redis_url: str, max_connections: int = 10
) -> redis.ConnectionPool:
    """
    Get or create a connection pool for the given Redis URL.
    Pools are cached by URL to reuse connections across RedisStreams instances.
    """
    pool_key = _pool_key(redis_url, max_connections)
    if pool_key not in _connection_pools:
        _connection_pools[pool_key] = redis.ConnectionPool.from_url(
            redis_url,
            decode_responses=True,
            max_connections=max_connections,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        _connection_pool_refs[pool_key] = 0
    _connection_pool_refs[pool_key] = _connection_pool_refs.get(pool_key, 0) + 1
    return _connection_pools[pool_key]


class RedisStreams:
    def __init__(self, redis_url: str, max_connections: int = 10):
        self._pool_key = _pool_key(redis_url, max_connections)
        self.pool = _get_connection_pool(redis_url, max_connections)
        self.r = redis.Redis(connection_pool=self.pool)

    def xadd_json(
        self, stream: str, obj: Dict[str, Any], maxlen: Optional[int] = 20000
    ) -> str:
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
                reclaimed = self._xautoclaim_json(
                    stream, group, consumer, count=count, min_idle_ms=min_idle_ms
                )
                if reclaimed:
                    return reclaimed

            resp = cast(
                List[Tuple[str, List[Tuple[str, Dict[str, Any]]]]],
                self.r.xreadgroup(
                    group, consumer, {stream: ">"}, count=count, block=block_ms
                ),
            )
        except ResponseError as e:
            if "NOGROUP" in str(e):
                self.r.xgroup_create(stream, group, id="0-0", mkstream=True)
                if recover_pending:
                    reclaimed = self._xautoclaim_json(
                        stream, group, consumer, count=count, min_idle_ms=min_idle_ms
                    )
                    if reclaimed:
                        return reclaimed
                resp = cast(
                    List[Tuple[str, List[Tuple[str, Dict[str, Any]]]]],
                    self.r.xreadgroup(
                        group, consumer, {stream: ">"}, count=count, block=block_ms
                    ),
                )
            else:
                raise

        out: List[Tuple[str, str, Dict[str, Any]]] = []
        for _, msgs in resp:
            for msg_id, fields in msgs:
                if "p" not in fields:
                    self._audit_missing_payload(stream, msg_id, fields)
                    raise ValueError(
                        f"missing payload field 'p' in stream={stream} id={msg_id}"
                    )
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
            resp = self.r.xautoclaim(
                stream, group, consumer, min_idle_ms, "0-0", count=count
            )  # type: ignore[attr-defined]
        except Exception:
            return []

        if not isinstance(resp, (list, tuple)) or len(resp) < 2:
            return []

        msgs = cast(List[Tuple[str, Dict[str, Any]]], resp[1] or [])
        out: List[Tuple[str, str, Dict[str, Any]]] = []
        for msg_id, fields in msgs:
            if "p" not in fields:
                self._audit_missing_payload(stream, msg_id, fields)
                raise ValueError(
                    f"missing payload field 'p' in stream={stream} id={msg_id}"
                )
            out.append((stream, msg_id, json.loads(fields["p"])))
        return out

    def xack(self, stream: str, group: str, msg_id: str) -> int:
        return cast(int, self.r.xack(stream, group, msg_id))

    def set_json(self, key: str, obj: Dict[str, Any], ex: Optional[int] = None) -> bool:
        return bool(self.r.set(key, json.dumps(obj, ensure_ascii=False), ex=ex))

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        v = cast(Optional[str], self.r.get(key))
        if not v:
            return None
        return json.loads(cast(str, v))

    def acquire_lock(self, key: str, ttl_s: int = 10) -> bool:
        return bool(self.r.set(key, "1", nx=True, ex=ttl_s))

    def release_lock(self, key: str) -> None:
        self.r.delete(key)

    def close(self) -> None:
        """Close all connections in the pool. Call when shutting down the service."""
        if self.pool and self._pool_key in _connection_pool_refs:
            _connection_pool_refs[self._pool_key] -= 1
            if _connection_pool_refs[self._pool_key] <= 0:
                self.pool.disconnect()
                _connection_pool_refs.pop(self._pool_key, None)
                _connection_pools.pop(self._pool_key, None)

    def _audit_missing_payload(
        self, stream: str, msg_id: str, fields: Dict[str, Any]
    ) -> None:
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
        self.xadd_json(
            "audit.logs", {"env": env, "data": data, "event": "stream_payload_missing"}
        )
