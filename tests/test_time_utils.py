import pytest
from datetime import datetime, timezone

from shared.time_utils import cycle_id_from_asof_minute, floor_to_minute
from shared.schemas import Envelope


def test_cycle_id_from_asof_minute_formats():
    assert cycle_id_from_asof_minute("2026-02-18T16:00:00Z") == "20260218T1600Z"
    assert cycle_id_from_asof_minute("2026-02-18T16:00Z") == "20260218T1600Z"


def test_cycle_id_from_asof_minute_rounds_down_seconds():
    assert cycle_id_from_asof_minute("2026-02-18T16:00:59Z") == "20260218T1600Z"


def test_floor_to_minute():
    dt = datetime(2026, 2, 18, 16, 0, 59, 999000, tzinfo=timezone.utc)
    floored = floor_to_minute(dt)
    assert floored == datetime(2026, 2, 18, 16, 0, 0, tzinfo=timezone.utc)


def test_envelope_cycle_id_validation():
    env = Envelope(source="test", cycle_id="20260218T1600Z")
    assert env.cycle_id == "20260218T1600Z"
    with pytest.raises(ValueError):
        Envelope(source="test", cycle_id="2026-02-18T16:00Z")
