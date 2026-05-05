# Phase 0 — First Metrics with Prometheus

> **Concepts introduced:** Metric, Counter, Gauge, Histogram, Labels, Scrape, Exposition format, PromQL, rate(), histogram_quantile()

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Metric** | A named, timestamped numerical measurement | The atomic unit of observability — everything in Prometheus is a metric |
| **Counter** | A metric that only ever increases | Request counts, error counts, bytes processed — anything that accumulates |
| **Gauge** | A metric that can go up and down | Active connections, queue depth, memory usage — current state |
| **Histogram** | Observations sampled into configurable buckets | Latency, payload size — required for percentile calculations |
| **Labels** | Key-value pairs attached to a metric | One time series per unique label combination — the source of Prometheus's power and its main scaling risk |
| **Scrape** | Prometheus pulling metrics from a target on a schedule | Pull model — Prometheus controls when it collects; targets do not push |
| **Exposition format** | Plain-text format served at `/metrics` | The contract between your app and Prometheus |
| **PromQL** | Prometheus Query Language | The language for querying time series data |
| **`rate()`** | Per-second average change of a counter over a time window | The correct way to query counters — handles resets automatically |
| **`histogram_quantile()`** | Calculates a percentile from histogram buckets | The only way to get P50/P95/P99 latency from Prometheus |

---

## The problem

> *Lumio — 12 engineers. Production has been running for 3 months.*
>
> At 2:14am on a Tuesday, Lumio's largest customer — a French fashion retailer — opened a support ticket: *"Your API is returning 503s. We're losing €40,000 per hour in abandoned checkout completions."*
>
> The on-call engineer woke up, SSHed into production, ran `docker logs lumio-api`. He saw errors. But when did they start? How many per minute? Which endpoint? Was it getting better or worse?
>
> He had no idea. He could read the present, not the past.
>
> He restarted the container. The errors stopped. He went back to sleep.
>
> The next morning the post-mortem had one line:
>
> *"Root cause: unknown. Resolution: container restart."*
>
> The CTO looked at that line for a long time.
>
> *"We need numbers over time. Rate of requests. Rate of errors. Latency at the 95th percentile. We need to know what happened at 2am, not just what's happening right now. We need Prometheus."*

---

## Architecture

```
phase-0-first-metrics/app/

  ┌─────────────┐    GET /metrics     ┌────────────────┐
  │  lumio-api  │◄───────────────────│   Prometheus   │
  │  :8000      │   every 15 seconds  │   :9090        │
  └─────────────┘                    └────────────────┘
       │                                     │
       │  POST /events                       │  PromQL queries
       │  GET  /events/summary               │  (manual, via UI)
       │  GET  /health                       ▼
       ▼                             http://localhost:9090
  http://localhost:8000
```

---

## Repository structure

```
phase-0-first-metrics/
└── app/
    ├── docker-compose.yml      ← starts api + prometheus
    ├── load.sh                 ← generates realistic traffic
    ├── api/
    │   ├── Dockerfile
    │   ├── app.py              ← Flask API with prometheus_client instrumentation
    │   └── requirements.txt
    └── prometheus/
        └── prometheus.yml      ← scrape config (what to collect and how often)
```

---

## Challenge 1 — Start the stack and explore the raw metrics

Before touching PromQL, understand what data you are working with.

### Step 1: Start the stack

```bash
cd phase-0-first-metrics/app
docker compose up -d --build
```

Wait for both containers to be healthy:

```bash
docker compose ps
```

Expected:
```
NAME                    STATUS
app-api-1               Up (healthy)
app-prometheus-1        Up
```

### Step 2: Call the API endpoints

```bash
# Health check
curl http://localhost:8000/health
# {"service":"lumio-api","status":"healthy","version":"0.1.0"}

# Ingest an event
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"type":"page_view","customer_id":"cust_123"}'
# {"event_id":"evt_1234567890","status":"accepted","type":"page_view"}

# Get aggregated summary
curl http://localhost:8000/events/summary
# {"by_type":{...},"total":42317,"window":"1h"}
```

### Step 3: Read the raw `/metrics` endpoint

```bash
curl http://localhost:8000/metrics
```

You will see a wall of text. This is the **Prometheus exposition format** — the contract between your app and Prometheus. Read a section carefully:

```
# HELP lumio_http_requests_total Total HTTP requests received
# TYPE lumio_http_requests_total counter
lumio_http_requests_total{endpoint="health",method="GET",status_code="200"} 1.0
lumio_http_requests_total{endpoint="ingest_event",method="POST",status_code="201"} 1.0
```

Each line has three parts:
1. `# HELP` — human-readable description (what the metric measures)
2. `# TYPE` — the metric type (counter, gauge, histogram, summary)
3. A sample line: `metric_name{label="value"} numerical_value`

### Step 4: Make a few more requests and re-read `/metrics`

```bash
curl -s http://localhost:8000/health > /dev/null
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"type":"cart_add"}' > /dev/null

curl http://localhost:8000/metrics | grep lumio_http_requests_total
```

The counter values have increased. Each scrape by Prometheus captures a snapshot of these values — stored as a time series.

---

## Challenge 2 — Understand the metrics in the code

Open the file and read through it:

```bash
cat phase-0-first-metrics/app/api/app.py
```

The file has two distinct sections: **metric definitions** (what to measure) and **instrumentation hooks** (when to record). Read them separately.

---

### Part 1 — The five metrics

#### `lumio_http_requests_total` — Counter

```python
REQUEST_COUNT = Counter(
    'lumio_http_requests_total',
    'Total HTTP requests received',
    ['method', 'endpoint', 'status_code']
)
```

A counter only ever goes up. It counts every HTTP request the API receives, broken down by three labels.

Labels create **dimensions**. Without them, you'd have one number: total requests ever. With three labels you can answer:
- *"How many POST requests came in?"* → filter `method="POST"`
- *"How many requests hit `/events`?"* → filter `endpoint="ingest_event"`
- *"How many returned a 503?"* → filter `status_code="503"`
- *"What is the error rate on `/events` specifically?"* → combine all three

Each unique combination of label values is stored as a **separate time series**. With 2 methods × 3 endpoints × ~5 status codes, this metric produces ~30 time series.

#### `lumio_http_request_duration_seconds` — Histogram

```python
REQUEST_LATENCY = Histogram(
    'lumio_http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'endpoint'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
```

A histogram records every observed latency value into one of several pre-defined buckets. Each bucket `b` counts all observations where `value <= b` — so they are cumulative. With these 10 buckets, Prometheus stores 12 time series per label combination: one per bucket (`_bucket{le="..."}`), one for the total count (`_count`), and one for the sum of all observed values (`_sum`).

The bucket boundaries matter. If your SLO is "95% of requests complete within 200ms" you need a bucket at `0.2`. Without it, `histogram_quantile` can only interpolate between `0.1` and `0.25`, introducing inaccuracy. **Define buckets before collecting data — they cannot be changed retroactively without losing history.**

The `status_code` label is intentionally absent from this metric. Latency per status code would multiply cardinality without adding useful information — you generally don't need P95 latency of your 404s.

#### `lumio_active_requests` — Gauge

```python
ACTIVE_REQUESTS = Gauge(
    'lumio_active_requests',
    'Number of in-flight HTTP requests'
)
```

Unlike a counter, a gauge can go up and down. It represents the current number of requests being processed right now — incremented when a request starts, decremented when it ends.

This is a **concurrency metric**, not a rate metric. You don't use `rate()` on a gauge — you just read its current value. A sustained high value indicates the API is under load or a slow endpoint is accumulating in-flight requests.

#### `lumio_events_processed_total` — Counter (business metric)

```python
EVENTS_PROCESSED = Counter(
    'lumio_events_processed_total',
    'Total events successfully processed',
    ['event_type']
)
```

This is a **business metric** — it measures what the service does, not just how the HTTP layer behaves. It answers: *"How many `checkout` events did we process in the last hour?"*

During the 2am incident, `lumio_http_requests_total` would tell you requests were failing. `lumio_events_processed_total` would tell you that checkouts had stopped completely — which is what the customer actually cared about. Business metrics are often more actionable during an incident than infrastructure metrics.

#### `lumio_events_errors_total` — Counter (error classification)

```python
EVENTS_ERRORS = Counter(
    'lumio_events_errors_total',
    'Total event processing errors',
    ['reason']
)
```

A dedicated error counter with a `reason` label. Rather than inferring errors from `status_code="503"` on the HTTP counter, this metric explicitly records what went wrong and why. Here it records `reason="upstream_timeout"` when the simulated upstream fails. In a real service, you'd add reasons like `validation_error`, `database_timeout`, or `rate_limited` — each becoming its own time series that you can alert on independently.

---

### Part 2 — The instrumentation hooks

The metric definitions say *what* to measure. The hooks say *when* to record.

```python
@app.before_request
def start_timer():
    g.start_time = time.time()
    if request.path != '/metrics':
        ACTIVE_REQUESTS.inc()
```

`before_request` runs before every route handler. It records the start time in Flask's per-request context object (`g`) so `after_request` can compute duration, and increments the active requests gauge.

```python
@app.teardown_request
def decrement_active(_exc):
    if request.path != '/metrics':
        ACTIVE_REQUESTS.dec()
```

`teardown_request` runs after every request — **even if an exception was raised**. This is important: if you decremented in `after_request`, an unhandled exception would skip the decrement and `lumio_active_requests` would leak upward over time. Using `teardown_request` guarantees the gauge is always decremented.

```python
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
```

`after_request` runs after the route handler returns a response (but before teardown). It computes duration from the start time stored in `g`, then records two observations: one into the latency histogram (`.observe(duration)`) and one increment on the request counter. The status code is only available here — after the handler has run and produced a response — which is why it is recorded in `after_request` and not `before_request`.

Note that `.labels(...)` returns a labelled version of the metric object — the actual counter or histogram bound to that specific label combination. You must call `.inc()` or `.observe()` on it, not on the base metric.

---

### Part 3 — Why `/metrics` is excluded from instrumentation

Every hook checks `if request.path != '/metrics'` before recording anything. This is intentional.

Prometheus scrapes `/metrics` every 15 seconds. If each scrape incremented `lumio_http_requests_total`, the `/metrics` endpoint would appear as the most-requested endpoint in your data — drowning out the signal from real traffic. It would also create a feedback loop: more scraping → more metrics data → larger `/metrics` response → more bytes to transfer per scrape.

Excluding `/metrics` from instrumentation keeps your data clean. The scraper is infrastructure, not traffic.

---

### Part 4 — Verify your understanding

After reading the code, make a request and check what changed:

```bash
# Make one successful POST
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"type":"checkout","customer_id":"cust_42"}' | python3 -m json.tool

# Check what incremented
curl -s http://localhost:8000/metrics | grep -E "^lumio_(http_requests_total|events_processed)"
```

You should see:
- `lumio_http_requests_total{endpoint="ingest_event",method="POST",status_code="201"}` incremented by 1
- `lumio_events_processed_total{event_type="checkout"}` incremented by 1 (unless the 2% error rate fired)

If you got a 503:
- `lumio_http_requests_total{...,status_code="503"}` incremented instead
- `lumio_events_errors_total{reason="upstream_timeout"}` incremented
- `lumio_events_processed_total` did **not** increment — the event was not processed

This is the separation of concerns between the HTTP layer metrics and the business layer metrics: one records the transport outcome, the other records the business outcome.

---

## Challenge 3 — Navigate the Prometheus UI

Open **http://localhost:9090** in a browser.

### Step 1: Check scrape targets

Navigate to **Status → Targets**.

You should see one target: `lumio-api` with state `UP`. This confirms Prometheus is successfully scraping your app every 15 seconds.

If the state is `DOWN`:
- Check `docker compose ps` — is the api container running?
- Check `docker compose logs api` — did the container start correctly?
- Verify the target address: `api:8000` resolves because both containers share the same Compose network.

### Step 2: Query the raw counter

Go to the **Graph** tab. In the query box, type:

```
lumio_http_requests_total
```

Click **Execute**. You will see a table of time series — one row per unique label combination. Switch to the **Graph** view. The lines climb steadily (if you are making requests) or are flat (if the app is idle).

This is a raw counter — the absolute count since the process started. It is not directly useful for alerting or dashboards, but it is the foundation for everything that follows.

### Step 3: Query the gauge

```
lumio_active_requests
```

This should be 0 (or briefly non-zero if you are actively sending requests). A gauge gives you the current state — no `rate()` needed.

---

## Challenge 4 — Your first PromQL: rate() and label filtering

### Why `rate()` instead of raw counter values

A counter increases monotonically. If you graph `lumio_http_requests_total`, you see a line climbing upward — useful for understanding cumulative totals, but impossible to compare across time periods or between services.

`rate()` converts a counter into a per-second rate, averaged over a time window:

```
rate(lumio_http_requests_total[5m])
```

This tells you: *"How many requests per second, on average, over the last 5 minutes?"*

> **The `[5m]` range selector:** The window must be at least 2× the scrape interval. With `scrape_interval: 15s`, use `[1m]` minimum. `[5m]` is a sensible default for most dashboards.

### Step 1: Total requests per second

```
sum(rate(lumio_http_requests_total[5m]))
```

`sum()` collapses all label dimensions into a single value — total RPS across all methods, endpoints, and status codes.

Run `./load.sh` in a separate terminal first, then query this. You should see a value around 8–10 RPS (the load script sends ~10 requests/sec).

### Step 2: Filter by label — error rate

```
rate(lumio_http_requests_total{status_code="503"}[5m])
```

Curly braces are label matchers. `=` is exact match, `!=` is not equal, `=~` is regex match, `!~` is negative regex.

To match all 5xx errors:

```
rate(lumio_http_requests_total{status_code=~"5.."}[5m])
```

### Step 3: Group by endpoint

```
sum by (endpoint)(rate(lumio_http_requests_total[5m]))
```

`sum by (endpoint)` keeps the `endpoint` label and sums away all others. This shows you RPS broken down by endpoint — the first question you ask during an incident: *"Which endpoint is the problem?"*

### Step 4: Error rate as a percentage

```
100 * sum(rate(lumio_http_requests_total{status_code=~"5.."}[5m]))
      /
      sum(rate(lumio_http_requests_total[5m]))
```

Divide errors by total requests, multiply by 100. This is the error rate percentage — one of the most important signals for any service.

> **When the denominator is zero:** If the app has received no requests, this query returns `NaN` (no data). In Grafana you can configure panels to show 0 instead of NaN with `or vector(0)`.

---

## Challenge 5 — Query latency with histograms

A histogram metric produces three sets of time series:

| Series | What it contains |
|---|---|
| `metric_bucket{le="0.05"}` | Count of observations ≤ 50ms |
| `metric_bucket{le="0.1"}` | Count of observations ≤ 100ms |
| `metric_bucket{le="+Inf"}` | Total count of all observations |
| `metric_count` | Same as `{le="+Inf"}` |
| `metric_sum` | Sum of all observed values |

### Step 1: Look at the raw bucket series

```
lumio_http_request_duration_seconds_bucket
```

You will see many time series — one per bucket per label combination. This is the raw data that `histogram_quantile()` uses to calculate percentiles.

### Step 2: Calculate P95 latency

```
histogram_quantile(0.95, rate(lumio_http_request_duration_seconds_bucket[5m]))
```

This is the most important latency query: *"What is the latency at the 95th percentile? 95% of requests complete within X seconds."*

### Step 3: Compare percentiles

Run these three queries and switch to the Graph view:

```
histogram_quantile(0.50, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
histogram_quantile(0.95, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
histogram_quantile(0.99, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
```

You will see the lines diverge: P50 is low and stable, P95 is higher, P99 is higher still and more variable. The gap between P95 and P99 is the "long tail" — the worst-performing 1% of requests.

> **`sum by(le)` before `histogram_quantile`:** The `le` label is the bucket boundary. You must preserve it in `sum by(le)` so `histogram_quantile` can see all the buckets. If you use `sum()` without `by(le)`, you collapse the buckets and get incorrect results.

### Step 4: Average latency (and why it misleads)

```
rate(lumio_http_request_duration_seconds_sum[5m])
/
rate(lumio_http_request_duration_seconds_count[5m])
```

This is the arithmetic mean latency. It is useful as a rough indicator but hides the tail. If 99% of requests complete in 10ms and 1% complete in 10 seconds, the average might be ~110ms — not representative of either group's experience.

Always prefer P95 or P99 for latency SLOs.

---

## Challenge 6 — Generate load and apply the RED method

The RED method defines the three queries every service needs:

| Signal | Question | PromQL |
|---|---|---|
| **R**ate | How many requests per second? | `sum(rate(lumio_http_requests_total[1m]))` |
| **E**rror rate | What percentage are failing? | `100 * sum(rate(lumio_http_requests_total{status_code=~"5.."}[1m])) / sum(rate(lumio_http_requests_total[1m]))` |
| **D**uration | How long do requests take (P95)? | `histogram_quantile(0.95, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[1m])))` |

### Step 1: Start the load generator

```bash
chmod +x phase-0-first-metrics/app/load.sh
./phase-0-first-metrics/app/load.sh
```

Leave it running and open the Prometheus UI in a separate browser tab.

### Step 2: Query each RED signal

In the Prometheus Graph tab, run each query above. Switch to the Graph view and set the time range to **Last 5 minutes**.

You should see:
- **Rate:** ~8–10 RPS (mostly `ingest_event` with some `health` and `events_summary`)
- **Error rate:** ~2% (the app simulates a 2% upstream error rate)
- **Duration (P95):** ~50–100ms (the `events/summary` endpoint is the slow one)

### Step 3: Identify the slowest endpoint

```
sum by (endpoint)(
  rate(lumio_http_request_duration_seconds_sum[1m])
)
/
sum by (endpoint)(
  rate(lumio_http_request_duration_seconds_count[1m])
)
```

This shows average latency broken down by endpoint. The `events_summary` endpoint (which simulates 50–200ms) should be significantly higher than `ingest_event` (5–50ms) and `health` (~1ms).

This is the first step in diagnosing a latency problem: isolate which endpoint is slow before investigating why.

### Step 4: Teardown

```bash
# Ctrl+C the load script first, then:
docker compose -f phase-0-first-metrics/app/docker-compose.yml down
```

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Build and start the stack in the background |
| `docker compose ps` | Check container status |
| `docker compose logs -f api` | Stream API logs |
| `docker compose down` | Stop and remove containers (data volume preserved) |
| `docker compose down -v` | Stop and remove containers + volumes (all data lost) |
| `curl http://localhost:9090/metrics` | Prometheus's own internal metrics |
| `curl http://localhost:8000/metrics` | App metrics in exposition format |

| PromQL | What it computes |
|---|---|
| `metric_name` | Instant vector — current value of all matching time series |
| `metric_name[5m]` | Range vector — values over the last 5 minutes |
| `rate(counter[5m])` | Per-second average rate of increase over 5 minutes |
| `sum(metric)` | Sum across all label combinations |
| `sum by (label)(metric)` | Sum, preserving the named label |
| `metric{label="value"}` | Filter by exact label value |
| `metric{label=~"regex"}` | Filter by regex label value |
| `histogram_quantile(0.95, rate(hist_bucket[5m]))` | 95th percentile from a histogram |

---

## What this doesn't do yet

| Issue | Impact | Fixed in |
|---|---|---|
| No dashboards — raw PromQL only | Requires PromQL knowledge during incidents; not shareable | Phase 1 |
| No alerting | Team learns about problems from customers, not from Prometheus | Phase 2 |
| No application-level context | Know requests are slow, can't tell which customer or event type | Phase 3 |
| No log aggregation | Metrics show something is wrong; can't find the log line | Phase 4 |
| Counter resets are handled by `rate()` but not visible | Process restarts are invisible without deployment markers | Phase 3 |

---

## Production considerations

### 1. Counter resets are normal — `rate()` handles them
When a process restarts, all counters reset to 0. `rate()` detects this automatically: if the current value is lower than the previous value, it treats it as a reset and uses 0 as the starting point. Never calculate `current - previous` manually — use `rate()`.

### 2. Choose histogram buckets that match your SLOs
The default prometheus_client buckets are `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]`. If your SLO is "95% of requests complete within 200ms", you need a bucket at 0.2. Without it, `histogram_quantile` interpolates linearly between 0.1 and 0.25, which may be inaccurate. Define buckets before you start collecting data — they cannot be changed retroactively.

### 3. Never use unbounded values as labels
Labels with high cardinality — user IDs, request IDs, email addresses, raw URLs with path parameters — create one time series per unique value. Ten million users means ten million time series. This will OOM Prometheus within hours. Labels should have a small, fixed set of values (endpoint names, HTTP methods, status code classes). This is covered in depth in Phase 8.

### 4. The range window must be at least 2× the scrape interval
`rate(metric[1m])` with `scrape_interval: 15s` gives you only 4 data points in the window — statistically unstable. Use at least `[1m]` for a 15s interval, `[5m]` for dashboards, `[2m]` for alerts.

### 5. Prometheus is not a long-term store
The default 15-day retention is suitable for operational monitoring. For capacity planning, cost analysis, or year-over-year comparisons, you need external long-term storage: Thanos, Grafana Mimir, or a remote_write endpoint. Plan your retention strategy before you need historical data.

---

## Outcome

The Lumio API now exposes five metrics covering the RED method (Rate, Error rate, Duration) plus active connections and business-level event counts. Prometheus scrapes these every 15 seconds and stores them as time series. You can query the last 7 days of data using PromQL in the Prometheus UI.

The 2am incident that produced "root cause: unknown" would now produce a graph: a spike in `rate(lumio_http_requests_total{status_code="503"}[5m])` starting at exactly 02:14, correlated with a flatline on `rate(lumio_events_processed_total[5m])`. That's a diagnosis, not a mystery.

Raw PromQL at 3am is still not an operational procedure. That is fixed in Phase 1.

---

[Back to main README](../README.md) | [Next: Phase 1 — Grafana Dashboards →](../phase-1-grafana/README.md)
