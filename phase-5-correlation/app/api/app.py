import os
import time
import random
import logging
import json
from datetime import datetime, timezone

from flask import Flask, request, jsonify

from opentelemetry import metrics, trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor

OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "otelcol:4317")
ENVIRONMENT   = os.getenv("ENVIRONMENT", "development")
SERVICE_NAME  = "lumio-api"

# ---------------------------------------------------------------------------
# OTel setup — identical to Phase 3
# ---------------------------------------------------------------------------
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME:           SERVICE_NAME,
    ResourceAttributes.SERVICE_VERSION:        "1.0.0",
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: ENVIRONMENT,
})

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True),
            export_interval_millis=15_000,
        )
    ],
)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(SERVICE_NAME)

tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
)
otel_trace.set_tracer_provider(tracer_provider)
tracer = otel_trace.get_tracer(SERVICE_NAME)

request_counter   = meter.create_counter("lumio.http.requests", unit="requests")
request_duration  = meter.create_histogram("lumio.http.request.duration", unit="s")
active_requests   = meter.create_up_down_counter("lumio.active_requests", unit="requests")
events_processed  = meter.create_counter("lumio.events.processed", unit="events")
events_errors     = meter.create_counter("lumio.events.errors", unit="errors")

# ---------------------------------------------------------------------------
# JSON structured logger — Phase 4 + trace_id injection (new in Phase 5)
#
# The key addition: every log line emitted inside an active span gets
# trace_id and span_id automatically embedded. No manual plumbing required.
#
# This means:
#   - From Loki: click trace_id → jump to that trace in Tempo
#   - From Tempo: click "Logs" on a span → search Loki for logs with that trace_id
# ---------------------------------------------------------------------------
_STANDARD_LOG_ATTRS = frozenset([
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
])


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":     record.levelname,
            "logger":    record.name,
            "service":   SERVICE_NAME,
            "message":   record.getMessage(),
        }

        # Inject the active span's trace context.
        # otel_trace.get_current_span() is always safe to call — returns a
        # no-op span when there is no active span, whose context is invalid.
        span = otel_trace.get_current_span()
        ctx  = span.get_span_context()
        if ctx.is_valid:
            log_obj["trace_id"] = format(ctx.trace_id, "032x")
            log_obj["span_id"]  = format(ctx.span_id,  "016x")

        # Attach any extra fields passed via logger.info("...", extra={...})
        for key, val in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and not key.startswith("_"):
                log_obj[key] = val

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


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
    active_requests.add(1, {"endpoint": request.endpoint or "unknown"})


@app.teardown_request
def teardown_request(exc):
    active_requests.add(-1, {"endpoint": request.endpoint or "unknown"})


@app.after_request
def after_request(response):
    duration = time.time() - request.start_time
    request_counter.add(1, {
        "method":      request.method,
        "endpoint":    request.endpoint or "unknown",
        "status_code": str(response.status_code),
    })
    request_duration.record(duration, {
        "method":   request.method,
        "endpoint": request.endpoint or "unknown",
    })
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.route("/events", methods=["POST"])
def ingest_event():
    event_type = random.choice(EVENT_TYPES)
    started_at = time.time()

    with tracer.start_as_current_span("process-event") as span:
        span.set_attribute("event.type", event_type)
        span.set_attribute("lumio.pipeline", "ingest")

        time.sleep(random.uniform(0.005, 0.05))
        duration_ms = round((time.time() - started_at) * 1000, 1)

        if random.random() < _error_rate:
            reason = random.choice(["validation_error", "schema_mismatch", "timeout"])
            span.set_attribute("error", True)
            span.set_attribute("error.reason", reason)
            events_errors.add(1, {"reason": reason})

            # trace_id injected automatically by JSONFormatter — no extra code needed
            logger.warning(
                "event processing failed",
                extra={
                    "event_type":   event_type,
                    "error_reason": reason,
                    "duration_ms":  duration_ms,
                },
            )
            return jsonify({"error": reason}), 500

        events_processed.add(1, {"event_type": event_type})
        logger.info(
            "event processed",
            extra={"event_type": event_type, "duration_ms": duration_ms},
        )
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
    logger.info("error rate changed", extra={"new_rate": _error_rate})
    return jsonify({"error_rate": _error_rate})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
