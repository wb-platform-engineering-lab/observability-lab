import os
import time
import random
from flask import Flask, jsonify, request, Response, g
from prometheus_client import (
    Counter, Gauge, Histogram,
    generate_latest, CONTENT_TYPE_LATEST
)

app = Flask(__name__)

# ── Metrics ────────────────────────────────────────────────────────────────────
#
# Three metric types used in this phase:
#
#   Counter   — monotonically increasing value (requests, events, errors)
#   Gauge     — value that can go up and down (active connections, queue depth)
#   Histogram — samples observations into buckets (latency, payload size)
#
# Every metric has a name, a help string, and optional labels.
# Labels create dimensions — one time series per unique label combination.

REQUEST_COUNT = Counter(
    'lumio_http_requests_total',
    'Total HTTP requests received',
    ['method', 'endpoint', 'status_code']
)

REQUEST_LATENCY = Histogram(
    'lumio_http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'endpoint'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

ACTIVE_REQUESTS = Gauge(
    'lumio_active_requests',
    'Number of in-flight HTTP requests'
)

EVENTS_PROCESSED = Counter(
    'lumio_events_processed_total',
    'Total events successfully processed',
    ['event_type']
)

EVENTS_ERRORS = Counter(
    'lumio_events_errors_total',
    'Total event processing errors',
    ['reason']
)

# ── Request instrumentation ────────────────────────────────────────────────────
#
# Flask's before_request / after_request / teardown_request hooks let us
# instrument every request in one place without touching individual route handlers.

@app.before_request
def start_timer():
    g.start_time = time.time()
    if request.path != '/metrics':
        ACTIVE_REQUESTS.inc()


@app.teardown_request
def decrement_active(_exc):
    # teardown_request runs even if an exception was raised — ensuring
    # ACTIVE_REQUESTS never gets stuck in an elevated state.
    if request.path != '/metrics':
        ACTIVE_REQUESTS.dec()


@app.after_request
def record_metrics(response):
    if request.path != '/metrics':
        duration = time.time() - g.start_time
        endpoint = request.endpoint or 'unknown'
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint
        ).observe(duration)
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=str(response.status_code)
        ).inc()
    return response


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/metrics')
def metrics():
    """Prometheus scrapes this endpoint every 15 seconds."""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'lumio-api',
        'version': os.getenv('APP_VERSION', '0.1.0')
    })


@app.route('/events', methods=['POST'])
def ingest_event():
    """
    Ingest a customer behaviour event.

    POST /events
    {"type": "page_view", "customer_id": "cust_123"}

    Simulates realistic processing time (5–50ms) and a ~2% upstream error rate.
    """
    time.sleep(random.uniform(0.005, 0.05))

    body = request.get_json(silent=True) or {}
    event_type = body.get('type', 'unknown')

    # Simulate ~2% upstream error rate
    if random.random() < 0.02:
        EVENTS_ERRORS.labels(reason='upstream_timeout').inc()
        return jsonify({'error': 'upstream service timeout'}), 503

    EVENTS_PROCESSED.labels(event_type=event_type).inc()
    return jsonify({
        'event_id': f'evt_{int(time.time() * 1000)}',
        'type': event_type,
        'status': 'accepted',
    }), 201


@app.route('/events/summary')
def events_summary():
    """
    Aggregated event counts for the last hour.
    Simulates a heavier read query (50–200ms).
    """
    time.sleep(random.uniform(0.05, 0.2))
    return jsonify({
        'window': '1h',
        'total': random.randint(10000, 100000),
        'by_type': {
            'page_view':    random.randint(5000, 60000),
            'cart_add':     random.randint(1000, 15000),
            'checkout':     random.randint(100,  3000),
            'search':       random.randint(2000, 20000),
            'product_view': random.randint(3000, 25000),
        }
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
