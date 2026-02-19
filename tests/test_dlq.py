from shared.bus.dlq import next_retry_count, with_retry_count


def test_next_retry_count_default():
    assert next_retry_count({"env": {}}) == 1


def test_next_retry_count_increments():
    assert next_retry_count({"env": {"retry_count": 2}}) == 3


def test_with_retry_count_sets_env():
    payload = {"env": {"retry_count": 1}, "data": {"x": 1}}
    updated = with_retry_count(payload, 5)
    assert updated["env"]["retry_count"] == 5
    assert payload["env"]["retry_count"] == 1
