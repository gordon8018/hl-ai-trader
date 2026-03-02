# shared/metrics/prom.py
from __future__ import annotations
import os
import sys
from prometheus_client import Counter, Histogram, Gauge, start_http_server

SERVICE = os.environ.get("SERVICE_NAME", "unknown")

MSG_IN = Counter("bus_messages_in_total", "Messages consumed from streams", ["service", "stream"])
MSG_OUT = Counter("bus_messages_out_total", "Messages produced to streams", ["service", "stream"])
ERR = Counter("service_errors_total", "Errors", ["service", "where"])
LAT = Histogram("service_latency_seconds", "Handler latency", ["service", "where"])

HEALTH = Gauge("service_health", "1 ok, 0 bad", ["service", "name"])
ALARM = Gauge("service_alarm", "1 alarm active", ["service", "name"])

LLM_CALLS = Counter("ai_llm_call_total", "LLM calls", ["service", "provider"])
LLM_ERRORS = Counter("ai_llm_error_total", "LLM errors", ["service", "reason"])
LLM_LAT_MS = Histogram("ai_llm_latency_ms", "LLM latency (ms)", ["service", "provider"])
AI_FALLBACK = Counter("ai_fallback_total", "AI fallback count", ["service", "reason"])
AI_CONFIDENCE = Gauge("ai_confidence", "AI confidence", ["service"])
AI_GROSS = Gauge("ai_gross", "AI gross exposure", ["service"])
AI_NET = Gauge("ai_net", "AI net exposure", ["service"])
AI_TURNOVER = Gauge("ai_turnover", "AI turnover", ["service"])

RISK_CLIP = Counter("risk_clip_total", "Risk clip events", ["service", "reason"])
RISK_NET_HITS = Counter("risk_net_cap_hits_total", "Net cap hits", ["service"])
RISK_GROSS_HITS = Counter("risk_gross_cap_hits_total", "Gross cap hits", ["service"])
RISK_TURNOVER_HITS = Counter("risk_turnover_cap_hits_total", "Turnover cap hits", ["service"])

EXEC_ORDERS = Counter("exec_orders_total", "Execution order events", ["service", "action", "status"])
EXEC_REJECT = Counter("exec_reject_total", "Execution rejects", ["service", "reason"])
EXEC_FILL_LAT_MS = Histogram("exec_fill_latency_ms", "Execution fill latency (ms)", ["service"])
EXEC_RATE_LIMIT = Counter("exec_rate_limited_total", "Execution rate limited", ["service", "type"])

def set_alarm(name: str, active: bool = True) -> None:
    ALARM.labels(SERVICE, name).set(1 if active else 0)

def start_metrics(port_env: str, default_port: int):
    enabled = os.environ.get("METRICS_ENABLED", "true").lower() != "false"
    if not enabled:
        return
    port = int(os.environ.get(port_env, str(default_port)))
    try:
        start_http_server(port)
        HEALTH.labels(SERVICE, "up").set(1)
    except OSError as e:
        # Don't crash trading services if metrics port cannot bind.
        print(
            f"[metrics] disabled for service={SERVICE} port={port} reason={e}",
            file=sys.stderr,
            flush=True,
        )
