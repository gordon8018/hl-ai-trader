# shared/hl/rate_limiter.py
from __future__ import annotations
import time
from dataclasses import dataclass
import redis

@dataclass
class TokenBucket:
    key: str
    rate_per_sec: float
    burst: int

class RedisTokenBucket:
    LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then
  tokens = burst
  ts = now
end

local delta = math.max(0, now - ts)
tokens = math.min(burst, tokens + delta * rate)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call("HMSET", key, "tokens", tokens, "ts", now)
redis.call("EXPIRE", key, 120)
return allowed
"""
    def __init__(self, redis_url: str):
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._sha = self.r.script_load(self.LUA)

    def allow(self, bucket: TokenBucket, cost: int = 1) -> bool:
        now = time.time()
        res = self.r.evalsha(self._sha, 1, bucket.key, now, bucket.rate_per_sec, bucket.burst, cost) # type: ignore
        return bool(int(res)) # type: ignore
