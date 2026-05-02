import os
import time
import random
import logging

from flask import Flask, request, jsonify

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lumio")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "otelcol:4317")   # OTel Collector gRPC endpoint
ENVIRONMENT   = os.getenv("ENVIRONMENT", "development")

# ---------------------------------------------------------------------------
# Resource — metadata attached to every metric, trace, and log this process emits.
# The Collector forwards these as labels / entity attributes to downstream backends.
# ---------------------------------------------------------------------------
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME:           "lumio-api",
    ResourceAttributes.SERVICE_VERSION:        "1.0.0",
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: ENVIRONMENT,
})

# ---------------------------------------------------------------------------
# Metrics pipeline — OTLP gRPC → OTel Collector → Prometheus exporter
# ---------------------------------------------------------------------------
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
meter = metrics.get_meter("lumio-api")

# ---------------------------------------------------------------------------
# Trace pipeline — OTLP gRPC → OTel Collector → Tempo
# ---------------------------------------------------------------------------
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
    )
)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("lumio-api")

# ---------------------------------------------------------------------------
# OTel instruments
#
# Naming: lumio.<domain>.<measurement>  (dot-separated, OTel convention)
# The Collector's Prometheus exporter converts these automatically:
#   lumio.http.requests           → lumio_http_requests_total
#   lumio.http.request.duration   → lumio_http_request_duration_seconds_{bucket,sum,count}
#   lumio.active_requests         → lumio_active_requests
# ---------------------------------------------------------------------------
request_counter = meter.create_counter(
    name="lumio.http.requests",
    unit="requests",
    description="Total HTTP requests handled",
)
request_duration = meter.create_histogram(
    name="lumio.http.request.duration",
    unit="s",
    description="HTTP request duration in seconds",
)
active_requests = meter.create_up_down_counter(
    name="lumio.active_requests",
    unit="requests",
    description="Number of in-flight HTTP requests",
)
events_processed = meter.create_counter(
    name="lumio.events.processed",
    unit="events",
    description="Events processed, labelled by type",
)
events_errors = meter.create_counter(
    name="lumio.events.errors",
    unit="errors",
    description="Event processing errors, labelled by reason",
)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)   # creates a root span for every HTTP request

EVENT_TYPES = ["page_view", "cart_add", "checkout", "search", "product_view"]


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
    attrs = {
        "method":      request.method,
        "endpoint":    request.endpoint or "unknown",
        "status_code": str(response.status_code),
    }
    request_counter.add(1, attrs)
    request_duration.record(duration, {"method": request.method, "endpoint": request.endpoint or "unknown"})
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "lumio-api"})


@app.route("/events", methods=["POST"])
def ingest_event():
    """Simulate event ingestion: 5–50ms processing, ~2% error rate."""
    event_type = random.choice(EVENT_TYPES)

    # Manual child span — adds business context to the auto-generated HTTP root span
    with tracer.start_as_current_span("process-event") as span:
        span.set_attribute("event.type", event_type)
        span.set_attribute("lumio.pipeline", "ingest")

        time.sleep(random.uniform(0.005, 0.05))

        if random.random() < 0.02:
            reason = random.choice(["validation_error", "schema_mismatch", "timeout"])
            span.set_attribute("error", True)
            span.set_attribute("error.reason", reason)
            events_errors.add(1, {"reason": reason})
            logger.warning("event processing failed type=%s reason=%s", event_type, reason)
            return jsonify({"error": reason}), 500

        events_processed.add(1, {"event_type": event_type})
        logger.info("event processed type=%s", event_type)
        return jsonify({"status": "accepted", "event_type": event_type}), 202


@app.route("/events/summary")
def events_summary():
    """Simulate a heavier aggregation query: 50–200ms."""
    with tracer.start_as_current_span("aggregate-events") as span:
        span.set_attribute("lumio.pipeline", "summary")

        with tracer.start_as_current_span("fetch-counts") as child:
            # Simulate a slow sub-operation — visible as a nested span in the trace
            time.sleep(random.uniform(0.03, 0.15))
            counts = {et: random.randint(100, 10000) for et in EVENT_TYPES}
            child.set_attribute("lumio.result.count", len(counts))

        with tracer.start_as_current_span("enrich-response"):
            time.sleep(random.uniform(0.01, 0.05))

        span.set_attribute("lumio.result.total", sum(counts.values()))
        return jsonify({"summary": counts})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
