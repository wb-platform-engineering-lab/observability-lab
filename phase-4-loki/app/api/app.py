import os
import time
import random
import logging
import json
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Structured JSON logger
#
# Every log line is a JSON object. Promtail parses these fields as labels
# and values in Loki, making logs searchable by field without regex.
#
# Example output:
#   {"timestamp":"2025-01-15T14:23:01.123Z","level":"INFO","logger":"lumio",
#    "message":"event processed","event_type":"checkout","duration_ms":23}
# ---------------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
        }
        # Attach any extra fields passed via logger.info("...", extra={...})
        for key, val in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "message", "module",
                "msecs", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName",
            ) and not key.startswith("_"):
                log_obj[key] = val
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("lumio")

# ---------------------------------------------------------------------------
# Metrics — same as Phase 2
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "lumio_http_requests_total", "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "lumio_http_request_duration_seconds", "HTTP request duration",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
ACTIVE_REQUESTS = Gauge("lumio_active_requests", "In-flight HTTP requests")
EVENTS_PROCESSED = Counter(
    "lumio_events_processed_total", "Events processed by type", ["event_type"],
)
EVENTS_ERRORS = Counter(
    "lumio_events_errors_total", "Event processing errors by reason", ["reason"],
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
    started_at = time.time()
    time.sleep(random.uniform(0.005, 0.05))
    duration_ms = round((time.time() - started_at) * 1000, 1)

    if random.random() < _error_rate:
        reason = random.choice(["validation_error", "schema_mismatch", "timeout"])
        EVENTS_ERRORS.labels(reason=reason).inc()
        logger.warning(
            "event processing failed",
            extra={
                "event_type":   event_type,
                "error_reason": reason,
                "duration_ms":  duration_ms,
            },
        )
        return jsonify({"error": reason}), 500

    EVENTS_PROCESSED.labels(event_type=event_type).inc()
    logger.info(
        "event processed",
        extra={
            "event_type":  event_type,
            "duration_ms": duration_ms,
        },
    )
    return jsonify({"status": "accepted", "event_type": event_type}), 202


@app.route("/events/summary")
def events_summary():
    started_at = time.time()
    time.sleep(random.uniform(0.05, 0.2))
    duration_ms = round((time.time() - started_at) * 1000, 1)
    counts = {et: random.randint(100, 10000) for et in EVENT_TYPES}

    logger.info(
        "summary generated",
        extra={
            "total_events": sum(counts.values()),
            "duration_ms":  duration_ms,
        },
    )
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
