from __future__ import annotations

from typing import Any, Dict, List, Tuple

from redis.exceptions import ResponseError


class FakeRedis:
    def __init__(self, decode_responses: bool = True):
        self.decode_responses = decode_responses
        self.streams: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        self.stream_groups: Dict[Tuple[str, str], str] = {}
        self.kv: Dict[str, Any] = {}
        self.hashes: Dict[str, Dict[str, Any]] = {}
        self._lua: Dict[str, str] = {}
        self._id = 0

    def xadd(self, stream: str, fields: Dict[str, Any], maxlen: int | None = None, approximate: bool = True) -> str:
        self._id += 1
        msg_id = f"{self._id}-0"
        self.streams.setdefault(stream, []).append((msg_id, dict(fields)))
        return msg_id

    def xgroup_create(self, stream: str, group: str, id: str = "0-0", mkstream: bool = False) -> bool:
        if mkstream and stream not in self.streams:
            self.streams[stream] = []
        self.stream_groups[(stream, group)] = id
        return True

    def xreadgroup(self, group: str, consumer: str, streams: Dict[str, str], count: int = 50, block: int = 0):
        for stream in streams:
            if (stream, group) not in self.stream_groups:
                raise ResponseError("NOGROUP No such key")
        out = []
        for stream in streams:
            msgs = self.streams.get(stream, [])
            if msgs:
                out.append((stream, msgs[:count]))
        return out

    def xack(self, stream: str, group: str, msg_id: str) -> int:
        return 1

    def xautoclaim(
        self,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        start: str,
        count: int = 50,
    ):
        # Minimal stub for tests: no pending messages reclaimed.
        return ("0-0", [], [])

    def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def get(self, key: str):
        return self.kv.get(key)

    def delete(self, key: str):
        if key in self.kv:
            del self.kv[key]

    def script_load(self, lua: str) -> str:
        sha = f"sha{len(self._lua) + 1}"
        self._lua[sha] = lua
        return sha

    def evalsha(self, sha: str, numkeys: int, key: str, now: float, rate: float, burst: int, cost: int):
        h = self.hashes.setdefault(key, {})
        tokens = h.get("tokens")
        ts = h.get("ts")

        if tokens is None:
            tokens = float(burst)
            ts = float(now)

        delta = max(0.0, float(now) - float(ts))
        tokens = min(float(burst), float(tokens) + delta * float(rate))

        allowed = 0
        if tokens >= float(cost):
            tokens -= float(cost)
            allowed = 1

        h["tokens"] = tokens
        h["ts"] = float(now)
        return allowed

    def hmget(self, key: str, *fields: str):
        h = self.hashes.get(key, {})
        return [h.get(f) for f in fields]

    def hmset(self, key: str, mapping: Dict[str, Any]):
        h = self.hashes.setdefault(key, {})
        for k, v in mapping.items():
            h[k] = v

    def expire(self, key: str, ttl: int):
        return True

    def xlen(self, stream: str) -> int:
        return len(self.streams.get(stream, []))
