import json

import pytest
from fastapi import HTTPException

from services.openclaw_ops import app as ops_app
from services.openclaw_ops.config import OpsConfig
from services.openclaw_ops.models import HealthCheckRequest, ReadStatusRequest, SetModeRequest
from services.openclaw_ops.smoke_check import ISSUE_SCRIPT_ERROR, run_smoke_check


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.kv = {}

    def xadd(self, stream, fields, maxlen=None, approximate=True):
        self.streams.setdefault(stream, []).append(("0-0", fields))
        return "0-0"

    def xrevrange(self, stream, count=1):
        items = self.streams.get(stream, [])
        if not items:
            return []
        return items[-count:]

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)


class FakeRedisClient:
    def __init__(self):
        self.r = FakeRedis()

    def xadd_stream(self, stream, payload, maxlen=20000):
        raw = json.dumps(payload)
        return str(self.r.xadd(stream, {"p": raw}, maxlen=maxlen, approximate=True))

    def read_last(self, stream, count):
        rows = self.r.xrevrange(stream, count=count)
        out = []
        for _, fields in rows:
            raw = fields.get("p")
            if isinstance(raw, str):
                out.append(json.loads(raw))
        return out

    def set_if_absent(self, key, value, ttl_s):
        raw = json.dumps(value)
        return bool(self.r.set(key, raw, nx=True, ex=ttl_s))

    def get_json(self, key):
        raw = self.r.get(key)
        return json.loads(raw) if raw else None


def setup_module():
    ops_app.CONFIG = OpsConfig(
        redis_url="redis://local",
        api_key="token",
        allowed_write_streams=["ctl.commands", "audit.logs", "ops.proposals"],
        allowed_read_streams=["state.snapshot"],
        idempotency_ttl=600,
    )
    ops_app.REDIS = FakeRedisClient()


def test_set_mode_writes_ctl_and_audit():
    req = SetModeRequest(mode="HALT", reason="test", source="ops", request_id="r1", evidence={})
    resp = ops_app.set_mode(req)
    streams = list(ops_app.REDIS.r.streams.keys())
    assert resp.ok is True
    assert "ctl.commands" in streams
    assert "audit.logs" in streams


def test_read_status_blocks_unauthorized_streams():
    req = ReadStatusRequest(streams=["exec.reports"], count=1)
    with pytest.raises(HTTPException) as exc:
        ops_app.read_status(req)
    assert exc.value.status_code == 403


def test_idempotent_request_id_behavior():
    req = SetModeRequest(mode="HALT", reason="first", source="ops", request_id="dup", evidence={})
    ops_app.set_mode(req)
    resp2 = ops_app.set_mode(req)
    assert resp2.deduped is True
    streams = ops_app.REDIS.r.streams
    assert len(streams["ctl.commands"]) == 1
    assert len(streams["audit.logs"]) == 1


def test_health_check_handles_script_failure(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("fail")

    import services.openclaw_ops.smoke_check as smoke

    monkeypatch.setattr(smoke, "subprocess", type("X", (), {"run": _boom}))
    metrics, notes = run_smoke_check(10, 10, 10)
    assert ISSUE_SCRIPT_ERROR in notes
