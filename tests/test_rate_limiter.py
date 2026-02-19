import redis

from shared.hl.rate_limiter import RedisTokenBucket, TokenBucket
import shared.hl.rate_limiter as rl
from tests.fake_redis import FakeRedis


def test_token_bucket_allows_then_denies(monkeypatch):
    r = FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *args, **kwargs: r))
    monkeypatch.setattr(rl.time, "time", lambda: 0.0)

    limiter = RedisTokenBucket("redis://localhost:6379/0")
    bucket = TokenBucket(key="rl:test", rate_per_sec=0.0, burst=2)

    assert limiter.allow(bucket, cost=1) is True
    assert limiter.allow(bucket, cost=1) is True
    assert limiter.allow(bucket, cost=1) is False
