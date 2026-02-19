from shared.bus.dlq import next_retry_count, with_retry_count, RetryPolicy, retry_or_dlq
from shared.schemas import Envelope


class DummyBus:
    def __init__(self):
        self.calls = []

    def xadd_json(self, stream, payload):
        self.calls.append((stream, payload))
        return "1-0"


def test_next_retry_count_default():
    assert next_retry_count({"env": {}}) == 1


def test_next_retry_count_increments():
    assert next_retry_count({"env": {"retry_count": 2}}) == 3


def test_with_retry_count_sets_env():
    payload = {"env": {"retry_count": 1}, "data": {"x": 1}}
    updated = with_retry_count(payload, 5)
    assert updated["env"]["retry_count"] == 5
    assert payload["env"]["retry_count"] == 1


def test_retry_or_dlq_retries_then_dlq():
    bus = DummyBus()
    policy = RetryPolicy(max_retries=1)
    env = Envelope(source="svc", cycle_id="20260218T1600Z").model_dump()

    payload = {"env": {"retry_count": 0}, "data": {"x": 1}}
    res1 = retry_or_dlq(bus, "s", payload, env, policy, reason="err")
    assert res1 == "retry"
    assert bus.calls[0][0] == "s"
    assert bus.calls[1][0] == "audit.logs"

    payload2 = {"env": {"retry_count": 1}, "data": {"x": 1}}
    res2 = retry_or_dlq(bus, "s", payload2, env, policy, reason="err")
    assert res2 == "dlq"
    assert bus.calls[-2][0] == "dlq.s"
    assert bus.calls[-1][0] == "audit.logs"
