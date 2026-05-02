import os
import time
import random
import logging
import json

from flask import Flask, request, jsonify
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Fixed version — used in Challenge 6.
#
# Changes from app.py:
#
#   1. user_id removed from REQUEST_COUNT labels.
#      The counter now has bounded cardinality:
#        5 endpoints × 2 methods × ~10 status codes ≈ 100 series maximum.
#
#   2. Per-user data moved to structured logs.
#      The log line carries user_id as a JSON field — queryable in Loki
#      (Phase 4) or any log aggregation system.  Logs are the right tool
#      for high-cardinality, per-entity data.  Metrics are for aggregates.
#
# To use: copy this file to app.py and rebuild.
#   cp app_fixed.py app.py
#   docker compose up --build -d api
# ---------------------------------------------------------------------------

# Structured JSON logger — same pattern as Phase 4
class JSONFormatter(logging.Formatter):
    def format(self, record):
        obj = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            obj.update(record.extra)
        return json.dumps(obj)

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("lumio")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


REQUEST_COUNT = Counter(
    "lumio_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],  # ← no user_id: bounded cardinality
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
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "lumio-api"})


@app.route("/events", methods=["POST"])
def ingest_event():
    event_type = random.choice(EVENT_TYPES)
    user_id = request.headers.get("X-User-ID", "anonymous")
    time.sleep(random.uniform(0.005, 0.05))
    if random.random() < _error_rate:
        reason = random.choice(["validation_error", "schema_mismatch", "timeout"])
        EVENTS_ERRORS.labels(reason=reason).inc()
        # Per-user failure data goes in the log, not in metric labels.
        # Loki (Phase 4) can query: {service="api"} | json | user_id = "user_42"
        logger.warning(
            "event processing failed",
            extra={"event_type": event_type, "reason": reason, "user_id": user_id},
        )
        return jsonify({"error": reason}), 500
    EVENTS_PROCESSED.labels(event_type=event_type).inc()
    logger.info(
        "event accepted",
        extra={"event_type": event_type, "user_id": user_id},
    )
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
    logger.info("error rate updated", extra={"error_rate": _error_rate})
    return jsonify({"error_rate": _error_rate})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
