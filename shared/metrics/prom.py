# shared/metrics/prom.py
from __future__ import annotations
import os
from prometheus_client import Counter, Histogram, Gauge, start_http_server

SERVICE = os.environ.get("SERVICE_NAME", "unknown")

MSG_IN = Counter("bus_messages_in_total", "Messages consumed from streams", ["service", "stream"])
MSG_OUT = Counter("bus_messages_out_total", "Messages produced to streams", ["service", "stream"])
ERR = Counter("service_errors_total", "Errors", ["service", "where"])
LAT = Histogram("service_latency_seconds", "Handler latency", ["service", "where"])

HEALTH = Gauge("service_health", "1 ok, 0 bad", ["service", "name"])
ALARM = Gauge("service_alarm", "1 alarm active", ["service", "name"])

def set_alarm(name: str, active: bool = True) -> None:
    ALARM.labels(SERVICE, name).set(1 if active else 0)

def start_metrics(port_env: str, default_port: int):
    port = int(os.environ.get(port_env, str(default_port)))
    start_http_server(port)
    HEALTH.labels(SERVICE, "up").set(1)
