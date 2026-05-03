"""
Lumio API — Phase 10 Capstone

Three observability signals, three separate pipelines:

  Metrics → prometheus_client → /metrics → Prometheus (scraped directly)
  Traces  → OTel SDK → otelcol:4317 → Tempo
  Logs    → JSON to stdout → promtail Docker SD → Loki

Each signal is independent.  A Prometheus scrape failure doesn't affect
traces.  A Tempo outage doesn't affect metrics.  Logs are always written
to stdout regardless of whether Loki is reachable.

The correlation between them:
  - Every log line inside an active OTel span carries trace_id + span_id
    injected by JSONFormatter, enabling Log → Trace navigation in Grafana.
  - Tempo's tracesToLogs config makes Trace → Logs navigation available.
  - Tempo's tracesToMetrics links traces to Prometheus panels by time window.
"""

import os
import time
import random
import logging
import json
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST,
)

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OTEL_ENDPOINT  = os.getenv("OTEL_ENDPOINT", "otelcol:4317")
ENVIRONMENT    = os.getenv("ENVIRONMENT", "production")
SERVICE_NAME   = "lumio-api"

# ---------------------------------------------------------------------------
# OTel — traces only (metrics are handled by prometheus_client below)
# ---------------------------------------------------------------------------
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME:           SERVICE_NAME,
    ResourceAttributes.SERVICE_VERSION:        "1.0.0",
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: ENVIRONMENT,
})

tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
)
otel_trace.set_tracer_provider(tracer_provider)
tracer = otel_trace.get_tracer(SERVICE_NAME)

# ---------------------------------------------------------------------------
# Prometheus metrics — scraped directly from /metrics
# These are the same metric names used throughout Phases 0–9 so all
# recording rules, dashboards, and alerts work without modification.
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "lumio_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "lumio_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
ACTIVE_REQUESTS = Gauge("lumio_active_requests", "In-flight requests")
EVENTS_PROCESSED = Counter(
    "lumio_events_processed_total",
    "Events accepted by type",
    ["event_type"],
)
EVENTS_ERRORS = Counter(
    "lumio_events_errors_total",
    "Event processing errors by reason",
    ["reason"],
)

# ---------------------------------------------------------------------------
# Structured JSON logger
# JSONFormatter injects trace_id + span_id from the active OTel span so
# every log line inside a request carries both signals automatically.
# ---------------------------------------------------------------------------
_STDLIB_ATTRS = frozenset([
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
])


class JSONFormatter(logging.Formatter):
    def format(self, record):
        obj = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":     record.levelname,
            "logger":    record.name,
            "service":   SERVICE_NAME,
            "env":       ENVIRONMENT,
            "message":   record.getMessage(),
        }
        span = otel_trace.get_current_span()
        ctx  = span.get_span_context()
        if ctx.is_valid:
            obj["trace_id"] = format(ctx.trace_id, "032x")
            obj["span_id"]  = format(ctx.span_id,  "016x")
        for k, v in record.__dict__.items():
            if k not in _STDLIB_ATTRS and not k.startswith("_"):
                obj[k] = v
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj)


_handler = logging.StreamHandler()
_handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("lumio")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)

EVENT_TYPES = ["page_view", "cart_add", "checkout", "search", "product_view"]
_error_rate = float(os.getenv("ERROR_RATE", "0.02"))


@app.before_request
def before_request():
    request.start_time = time.time()
    ACTIVE_REQUESTS.inc()


@app.teardown_request
def teardown_request(exc):
    ACTIVE_REQUESTS.dec()


@app.after_request
def after_request(response):
    duration = time.time() - request.start_time
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.endpoint or "unknown",
        status_code=str(response.status_code),
    ).inc()
    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.endpoint or "unknown",
    ).observe(duration)
    return response


@app.route("/metrics")
def metrics_endpoint():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME, "env": ENVIRONMENT})


@app.route("/events", methods=["POST"])
def ingest_event():
    event_type = random.choice(EVENT_TYPES)

    with tracer.start_as_current_span("process-event") as span:
        span.set_attribute("event.type", event_type)
        span.set_attribute("lumio.pipeline", "ingest")
        time.sleep(random.uniform(0.005, 0.05))

        if random.random() < _error_rate:
            reason = random.choice(["validation_error", "schema_mismatch", "timeout"])
            span.set_attribute("error", True)
            span.set_attribute("error.reason", reason)
            EVENTS_ERRORS.labels(reason=reason).inc()
            logger.warning(
                "event processing failed",
                extra={"event_type": event_type, "error_reason": reason},
            )
            return jsonify({"error": reason}), 500

        EVENTS_PROCESSED.labels(event_type=event_type).inc()
        logger.info("event processed", extra={"event_type": event_type})
        return jsonify({"status": "accepted", "event_type": event_type}), 202


@app.route("/events/summary")
def events_summary():
    with tracer.start_as_current_span("aggregate-events") as span:
        span.set_attribute("lumio.pipeline", "summary")

        with tracer.start_as_current_span("fetch-counts") as child:
            time.sleep(random.uniform(0.03, 0.15))
            counts = {et: random.randint(100, 10000) for et in EVENT_TYPES}
            child.set_attribute("lumio.result.count", len(counts))

        with tracer.start_as_current_span("enrich-response"):
            time.sleep(random.uniform(0.01, 0.05))

        total = sum(counts.values())
        span.set_attribute("lumio.result.total", total)
        logger.info("summary generated", extra={"total": total})
        return jsonify({"summary": counts})


@app.route("/admin/set-error-rate", methods=["POST"])
def set_error_rate():
    global _error_rate
    data = request.get_json() or {}
    _error_rate = float(data.get("rate", 0.02))
    logger.info("error rate updated", extra={"new_rate": _error_rate})
    return jsonify({"error_rate": _error_rate})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
