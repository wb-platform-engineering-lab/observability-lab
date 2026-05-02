import os
import time
import random
import logging

from flask import Flask, request, jsonify
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lumio")

# ---------------------------------------------------------------------------
# Metrics
#
# ⚠️  CARDINALITY BUG — intentionally introduced for Phase 8.
#
# A developer added `user_id` as a label to lumio_http_requests_total.
# The intent was to track per-user request counts in Prometheus.
# The effect: one new time series per unique user_id per
# (method × endpoint × status_code) combination.
#
# With 50 000 active users × 5 endpoints × 2 methods × 3 status codes
# = 1 500 000 potential time series.  Each series consumes ~1–3 KB of
# RAM in Prometheus's head block.  At 1.5M series that is 1.5–4.5 GB —
# enough to OOM-kill Prometheus on a standard instance.
#
# This file is the starting point.  The challenges walk you through
# detection, a hotfix (metric_relabel_configs), and the proper fix
# (removing user_id from the metric and using structured logs instead).
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "lumio_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "user_id"],  # ← user_id is the problem
)
REQUEST_LATENCY = Histogram(
    "lumio_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
ACTIVE_REQUESTS = Gauge("lumio_active_requests", "In-flight HTTP requests")
EVENTS_PROCESSED = Counter(
    "lumio_events_processed_total",
    "Events processed by type",
    ["event_type"],
)
EVENTS_ERRORS = Counter(
    "lumio_events_errors_total",
    "Event processing errors by reason",
    ["reason"],
)

_error_rate = float(os.getenv("ERROR_RATE", "0.02"))

app = Flask(__name__)
EVENT_TYPES = ["page_view", "cart_add", "checkout", "search", "product_view"]


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
    # Read the user_id from the request header.  Every unique value creates
    # a new time series in Prometheus.  The cardinality.sh load script sends
    # a different random user_id on every request.
    user_id = request.headers.get("X-User-ID", "anonymous")
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.endpoint or "unknown",
        status_code=str(response.status_code),
        user_id=user_id,
    ).inc()
    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.endpoint or "unknown",
    ).observe(duration)
    return response


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "lumio-api"})


@app.route("/events", methods=["POST"])
def ingest_event():
    event_type = random.choice(EVENT_TYPES)
    time.sleep(random.uniform(0.005, 0.05))
    if random.random() < _error_rate:
        reason = random.choice(["validation_error", "schema_mismatch", "timeout"])
        EVENTS_ERRORS.labels(reason=reason).inc()
        user_id = request.headers.get("X-User-ID", "anonymous")
        logger.warning(
            "event processing failed type=%s reason=%s user_id=%s",
            event_type, reason, user_id,
        )
        return jsonify({"error": reason}), 500
    EVENTS_PROCESSED.labels(event_type=event_type).inc()
    return jsonify({"status": "accepted", "event_type": event_type}), 202


@app.route("/events/summary")
def events_summary():
    time.sleep(random.uniform(0.05, 0.2))
    counts = {et: random.randint(100, 10000) for et in EVENT_TYPES}
    return jsonify({"summary": counts})


@app.route("/admin/set-error-rate", methods=["POST"])
def set_error_rate():
    global _error_rate
    data = request.get_json() or {}
    _error_rate = float(data.get("rate", 0.02))
    logger.info("error rate set to %.2f", _error_rate)
    return jsonify({"error_rate": _error_rate})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
