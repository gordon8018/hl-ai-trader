def test_set_alarm_toggle():
    import shared.metrics.prom as prom
    prom.set_alarm("error_streak", True)
    assert prom.ALARM.labels(prom.SERVICE, "error_streak")._value.get() == 1.0
    prom.set_alarm("error_streak", False)
    assert prom.ALARM.labels(prom.SERVICE, "error_streak")._value.get() == 0.0
