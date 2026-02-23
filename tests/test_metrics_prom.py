def test_set_alarm_toggle():
    import shared.metrics.prom as prom
    prom.set_alarm("error_streak", True)
    assert prom.ALARM.labels(prom.SERVICE, "error_streak")._value.get() == 1.0
    prom.set_alarm("error_streak", False)
    assert prom.ALARM.labels(prom.SERVICE, "error_streak")._value.get() == 0.0


def test_start_metrics_bind_error_does_not_raise(monkeypatch):
    import shared.metrics.prom as prom

    def boom(_port):
        raise PermissionError("bind denied")

    monkeypatch.setattr(prom, "start_http_server", boom)
    monkeypatch.setenv("METRICS_ENABLED", "true")
    prom.start_metrics("METRICS_PORT", 9999)
