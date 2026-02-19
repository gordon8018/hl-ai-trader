import pytest

from shared.bus.guard import require_env
from shared.bus.publish import _normalize_env, publish
from shared.schemas import Envelope


def test_require_env_missing_raises():
    with pytest.raises(ValueError):
        require_env({"data": {"x": 1}})


def test_require_env_valid_normalizes():
    env = Envelope(source="test", cycle_id="20260218T1600Z").model_dump()
    payload = require_env({"env": env, "data": {"x": 1}})
    assert payload["env"]["cycle_id"] == "20260218T1600Z"
    assert payload["env"]["source"] == "test"


def test_normalize_env_infers_source():
    env_dict = {"cycle_id": "20260218T1600Z"}
    normalized = _normalize_env(env_dict, default_source="fallback")
    assert normalized["source"] == "fallback"
    assert normalized["cycle_id"] == "20260218T1600Z"


def test_normalize_env_none_creates_env():
    normalized = _normalize_env(None, default_source="audit")
    assert normalized["source"] == "audit"
    assert "cycle_id" in normalized


class DummyBus:
    def __init__(self):
        self.calls = []

    def xadd_json(self, stream, payload):
        self.calls.append((stream, payload))
        return "1-0"


def test_publish_writes_payload():
    bus = DummyBus()
    msg_id = publish(
        bus,
        "audit.logs",
        env={"cycle_id": "20260218T1600Z"},
        event="test",
        data={"x": 1},
        source="svc",
    )
    assert msg_id == "1-0"
    stream, payload = bus.calls[0]
    assert stream == "audit.logs"
    assert payload["env"]["source"] == "svc"
    assert payload["event"] == "test"
    assert payload["data"]["x"] == 1
