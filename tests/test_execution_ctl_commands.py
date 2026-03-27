import importlib
import json
import os
import sys
import types


def _install_redis_stub():
    if "redis" in sys.modules and "redis.exceptions" in sys.modules:
        return

    redis_mod = types.ModuleType("redis")
    exceptions_mod = types.ModuleType("redis.exceptions")

    class _RedisError(Exception):
        pass

    class ResponseError(_RedisError):
        pass

    class TimeoutError(_RedisError):
        pass

    class ConnectionError(_RedisError):
        pass

    class ConnectionPool:
        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        def disconnect(self):
            return None

    class Redis:
        def __init__(self, *args, **kwargs):
            pass

    redis_mod.ConnectionPool = ConnectionPool
    redis_mod.Redis = Redis
    exceptions_mod.ResponseError = ResponseError
    exceptions_mod.TimeoutError = TimeoutError
    exceptions_mod.ConnectionError = ConnectionError
    redis_mod.exceptions = exceptions_mod

    sys.modules["redis"] = redis_mod
    sys.modules["redis.exceptions"] = exceptions_mod


def _install_prometheus_stub():
    if "prometheus_client" in sys.modules:
        return

    prom_mod = types.ModuleType("prometheus_client")

    class _Metric:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            return None

        def observe(self, *args, **kwargs):
            return None

    def start_http_server(*args, **kwargs):
        return None

    prom_mod.Counter = _Metric
    prom_mod.Histogram = _Metric
    prom_mod.Gauge = _Metric
    prom_mod.start_http_server = start_http_server

    sys.modules["prometheus_client"] = prom_mod


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    original_modules = {
        name: sys.modules.get(name)
        for name in ("redis", "redis.exceptions", "prometheus_client", "services.execution.app")
    }
    _install_redis_stub()
    _install_prometheus_stub()
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        return importlib.import_module(mod_name)
    finally:
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_normalize_ctl_payload_accepts_wrapped_p_message():
    mod = load_module()
    wrapped = {
        "p": json.dumps(
            {
                "env": {
                    "event_id": "e1",
                    "ts": "2026-03-27T03:00:00Z",
                    "source": "openclaw_ops",
                    "cycle_id": "20260327T0300Z",
                    "retry_count": 0,
                    "schema_version": "1.0",
                },
                "data": {"cmd": "RESUME", "reason": "test"},
            }
        )
    }

    normalized = mod.normalize_ctl_payload(wrapped)

    assert normalized["data"]["cmd"] == "RESUME"
    assert normalized["env"]["source"] == "openclaw_ops"


def test_normalize_ctl_payload_rejects_invalid_wrapped_message():
    mod = load_module()

    try:
        mod.normalize_ctl_payload({"p": "{not-json"})
        raised = False
    except ValueError as e:
        raised = True
        assert "invalid_wrapped_payload" in str(e)

    assert raised is True


def test_should_ack_ctl_apply_error_requires_dlq_write():
    mod = load_module()

    assert mod.should_ack_ctl_apply_error(True) is True
    assert mod.should_ack_ctl_apply_error(False) is True
