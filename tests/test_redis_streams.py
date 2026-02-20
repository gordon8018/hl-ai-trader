import redis
import pytest

from shared.bus.redis_streams import RedisStreams
from tests.fake_redis import FakeRedis


@pytest.fixture
def bus(monkeypatch):
    r = FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *args, **kwargs: r))
    return RedisStreams("redis://localhost:6379/0")


def test_xadd_xreadgroup_roundtrip(bus):
    msg_id = bus.xadd_json("test.stream", {"k": 1})
    assert msg_id is not None

    msgs = bus.xreadgroup_json("test.stream", "g1", "c1", count=1, block_ms=1)
    assert len(msgs) == 1
    stream, mid, payload = msgs[0]
    assert stream == "test.stream"
    assert mid == msg_id
    assert payload == {"k": 1}


def test_set_get_json(bus):
    assert bus.set_json("k", {"a": 1}, ex=10) is True
    assert bus.get_json("k") == {"a": 1}


def test_missing_payload_audited(bus):
    # insert raw message without 'p'
    bus.r.xadd("bad.stream", {"x": "1"})
    with pytest.raises(ValueError):
        bus.xreadgroup_json("bad.stream", "g2", "c2", count=1, block_ms=1)

    assert bus.r.xlen("audit.logs") == 1


def test_xautoclaim_reclaims_pending(bus, monkeypatch):
    msg = {"p": "{\"k\":2}"}

    def fake_xautoclaim(stream, group, consumer, min_idle_ms, start, count=50):
        return ("0-0", [("9-0", msg)], [])

    monkeypatch.setattr(bus.r, "xautoclaim", fake_xautoclaim)
    msgs = bus.xreadgroup_json("test.stream", "g3", "c3", count=1, block_ms=1, recover_pending=True)
    assert len(msgs) == 1
    assert msgs[0][1] == "9-0"
    assert msgs[0][2] == {"k": 2}
