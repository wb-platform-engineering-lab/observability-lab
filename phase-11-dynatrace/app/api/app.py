import os
import time
import random
import logging

from flask import Flask, request, jsonify, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.flask import FlaskInstrumentor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lumio")

# ---------------------------------------------------------------------------
# Dynatrace connection — optional. If env vars are absent the app runs
# with Prometheus-only metrics and no traces exported.
# ---------------------------------------------------------------------------
DT_ENDPOINT  = os.getenv("DT_ENDPOINT", "").rstrip("/")   # https://xyz.live.dynatrace.com/api/v2/otlp
DT_API_TOKEN = os.getenv("DT_API_TOKEN", "")
ENVIRONMENT  = os.getenv("ENVIRONMENT", "development")

dt_enabled = bool(DT_ENDPOINT and DT_API_TOKEN)
if dt_enabled:
    logger.info("Dynatrace OTLP export enabled: %s", DT_ENDPOINT)
else:
    logger.info("Dynatrace OTLP export disabled — set DT_ENDPOINT and DT_API_TOKEN to enable")

# ---------------------------------------------------------------------------
# OTel resource — these attributes appear in Dynatrace as service metadata
# ---------------------------------------------------------------------------
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME:        "lumio-api",
    ResourceAttributes.SERVICE_VERSION:     "1.0.0",
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: ENVIRONMENT,
})

# ---------------------------------------------------------------------------
# Metrics pipeline — dual: Prometheus scrape (always) + OTLP push (optional)
# ---------------------------------------------------------------------------
prometheus_reader = PrometheusMetricReader()
metric_readers = [prometheus_reader]

if dt_enabled:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    metric_readers.append(
        PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=f"{DT_ENDPOINT}/v1/metrics",
                headers={"Authorization": f"Api-Token {DT_API_TOKEN}"},
            ),
            export_interval_millis=60_000,
        )
    )

meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("lumio-api")

# ---------------------------------------------------------------------------
# Trace pipeline — OTLP push to Dynatrace (optional)
# ---------------------------------------------------------------------------
tracer_provider = TracerProvider(resource=resource)

if dt_enabled:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{DT_ENDPOINT}/v1/traces",
                headers={"Authorization": f"Api-Token {DT_API_TOKEN}"},
            )
        )
    )

trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("lumio-api")

# ---------------------------------------------------------------------------
# OTel instruments
#
# Naming convention: lumio.<domain>.<measurement>
# OTel Prometheus exporter maps:
#   counter  lumio.http.requests           → lumio_http_requests_total
#   histogram lumio.http.request.duration  → lumio_http_request_duration_seconds_*
#   updown   lumio.active_requests         → lumio_active_requests
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
    description="Events processed, by type",
)
events_errors = meter.create_counter(
    name="lumio.events.errors",
    unit="errors",
    description="Event processing errors, by reason",
)

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)   # auto-creates spans for every request

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
    labels = {
        "method":      request.method,
        "endpoint":    request.endpoint or "unknown",
        "status_code": str(response.status_code),
    }
    request_counter.add(1, labels)
    request_duration.record(duration, {"method": request.method, "endpoint": request.endpoint or "unknown"})
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "lumio-api"})


@app.route("/metrics")
def prometheus_metrics():
    """Prometheus scrape endpoint — served by the OTel Prometheus exporter."""
    return Response(generate_latest(REGISTRY), status=200, mimetype=CONTENT_TYPE_LATEST)


@app.route("/events", methods=["POST"])
def ingest_event():
    """Simulate event ingestion: 5–50ms processing, ~2% error rate."""
    event_type = random.choice(EVENT_TYPES)

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
        time.sleep(random.uniform(0.05, 0.2))

        counts = {et: random.randint(100, 10000) for et in EVENT_TYPES}
        span.set_attribute("lumio.result.total", sum(counts.values()))
        return jsonify({"summary": counts})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
