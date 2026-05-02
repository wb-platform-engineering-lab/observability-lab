# Phase 8 — Cardinality & Production Pitfalls

> **Lumio, 60 engineers.**
> A developer opens a post-mortem ticket: *"I added `user_id` as a label to `lumio_http_requests_total` last Tuesday so we could track per-user request counts in Grafana.  Prometheus OOM-killed itself at 4am Saturday.  All dashboards went dark.  We had no metrics for six hours."*
>
> The root cause: Lumio now has 50 000 active users.  Every unique user_id × endpoint × method × status_code combination is a new time series.  At peak, Prometheus was tracking 900 000 series.  At ~2 KB each that's ~1.8 GB — more than the container memory limit.  The OOM killer terminated the process.

## What you will learn

- What Prometheus cardinality is and why it kills instances
- How to detect an explosion in progress (TSDB status page, PromQL, the Grafana dashboard)
- How to apply an emergency hotfix with `metric_relabel_configs` — zero downtime, no app restart
- How to fix the root cause properly in application code
- The four common cardinality anti-patterns and how to avoid them
- How to write proactive alerts that catch the next explosion before it hurts

---

## Concepts

### What is cardinality?

Every Prometheus time series is uniquely identified by its **metric name plus the complete set of label key-value pairs**.  Cardinality is the number of distinct time series that exist.

```
# These are THREE different time series — same metric, different label values:
lumio_http_requests_total{method="GET",  endpoint="health",  status_code="200", user_id="user_1"}
lumio_http_requests_total{method="GET",  endpoint="health",  status_code="200", user_id="user_2"}
lumio_http_requests_total{method="POST", endpoint="events",  status_code="500", user_id="user_1"}
```

The number of series for a metric = the product of the unique values for each label:

```
lumio_http_requests_total series count
  = unique methods × unique endpoints × unique status_codes × unique user_ids
  = 2 × 5 × 3 × 50 000
  = 1 500 000 series
```

### Why high cardinality kills Prometheus

Each active time series lives in the **TSDB head block** — Prometheus's in-memory write buffer.  The head block holds the most recent 2 hours of data (configurable).

Memory cost per series: ~1–3 KB (index entry + active chunk + symbol table entries).

| Series count | Approximate RAM |
|---|---|
| 1 000 | ~2 MB |
| 10 000 | ~20 MB |
| 100 000 | ~200 MB |
| 1 000 000 | ~2 GB |
| 10 000 000 | ~20 GB |

At 1.5M series Prometheus needs ~3 GB.  Most lab setups give Docker 2–4 GB total.  OOM kill.

Beyond memory:
- **Scrape latency increases**: the `/metrics` endpoint must enumerate all 1.5M series on every 15s scrape
- **Query latency increases**: every `rate(lumio_http_requests_total[5m])` scans all 1.5M series
- **Storage grows linearly**: each series writes samples to disk every 15s — at 1.5M series that's ~100k samples/sec

### The four anti-patterns

**1. Unbounded entity IDs as labels**

Labels whose values come from user-generated content or unbounded identifier spaces:

```python
# ❌ Bad — user_id can be any string, cardinality is unbounded
REQUEST_COUNT.labels(user_id=user_id, endpoint=endpoint).inc()

# ✅ Good — finite, known set of values
REQUEST_COUNT.labels(endpoint=endpoint, status_code=status_code).inc()
# Put user_id in a structured log line instead
logger.info("request", extra={"user_id": user_id, "endpoint": endpoint})
```

Common offenders: `user_id`, `session_id`, `request_id`, `trace_id`, `customer_id`, `order_id`

**2. URL paths with embedded IDs**

```python
# ❌ Bad — endpoint becomes /api/users/12345, /api/users/67890, ...
endpoint = request.path        # /api/users/12345
REQUEST_COUNT.labels(endpoint=endpoint).inc()

# ✅ Good — use the route pattern, not the URL
endpoint = request.endpoint    # "get_user" (Flask endpoint name)
# or
endpoint = "/api/users/<id>"   # normalised route template
```

**3. Histogram buckets with too many custom boundaries**

```python
# ❌ Bad — 50 buckets × 5 endpoints × 2 methods = 500 bucket series
REQUEST_LATENCY = Histogram("...", buckets=[i * 0.01 for i in range(50)])

# ✅ Good — 10 buckets is usually sufficient
REQUEST_LATENCY = Histogram("...", buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0])
```

Each histogram metric emits `N_buckets + 2` series per label combination (`_count`, `_sum`, and one per `le` boundary).

**4. Labels from external, uncontrolled sources**

```python
# ❌ Bad — User-Agent can be any string; thousands of distinct browser strings
REQUEST_COUNT.labels(user_agent=request.headers.get("User-Agent")).inc()

# ✅ Good — normalise to a known set
ua = request.headers.get("User-Agent", "")
agent_type = "mobile" if "Mobile" in ua else "browser" if "Mozilla" in ua else "api"
REQUEST_COUNT.labels(client_type=agent_type).inc()
```

### metric_relabel_configs: the emergency hotfix

When a high-cardinality label is already in production, you can't just delete the metric — it's being used by dashboards and alerts.  `metric_relabel_configs` lets you transform or drop labels **at scrape time, before they're written to the TSDB**.

```yaml
scrape_configs:
  - job_name: lumio-api
    static_configs:
      - targets: ["api:8000"]
    metric_relabel_configs:
      # Drop the user_id label from every incoming metric.
      # lumio_http_requests_total{method="GET", endpoint="events",
      #   status_code="200", user_id="user_12345"}
      # becomes:
      # lumio_http_requests_total{method="GET", endpoint="events",
      #   status_code="200"}
      - regex: user_id
        action: labeldrop
```

Effect: all series with different `user_id` values are collapsed into a single series per remaining label combination.  Existing `user_id`-based series in the TSDB become stale after 5 minutes and are eventually garbage-collected.  Series count drops.

**This is a hotfix, not a fix.**  The application still generates the label on every request.  The waste happens in the app and in the scrape response — Prometheus just discards it before writing.  The proper fix is to remove the label from the metric definition in the application code.

### The TSDB status page

Prometheus has a built-in cardinality report at `/tsdb-status`.

http://localhost:9090/tsdb-status

It shows (computed internally — efficient even at high cardinality):
- **Top series by metric name** — which metric has the most time series
- **Top series by label name** — which label name contributes most to cardinality
- **Top series by label value pair** — which specific label=value pair appears in the most series

This page is your first stop when diagnosing a cardinality incident.  Always check it before running `topk` PromQL queries, which can themselves be expensive at high cardinality.

---

## Stack

```
┌──────────────────────────────────────────────────────────┐
│  docker compose up                                       │
│                                                          │
│  api:8000        ← app.py with user_id label bug         │
│  prometheus:9090 ← scrapes api + itself                  │
│  alertmanager:9093                                       │
│  webhook:5001                                            │
│  grafana:3000    ← API dashboard + Cardinality Monitor   │
└──────────────────────────────────────────────────────────┘
```

The stack starts with the cardinality bug already present in `api/app.py`.  The challenges walk you through detecting it, applying a hotfix, and correcting it properly.

---

## Challenges

### Challenge 1 — Start the stack and establish a baseline

```bash
cd phase-8-cardinality/app
docker compose up --build -d
```

Before generating any load, record the baseline series count:

```bash
curl -sg 'http://localhost:9090/api/v1/query?query=prometheus_tsdb_head_series' \
  | python3 -m json.tool
```

Note the value.  With no traffic, you'll see ~200–400 series (Prometheus's own self-monitoring metrics).

Open the **Cardinality Monitor** dashboard: http://localhost:3000/d/lumio-cardinality

Start the normal load generator (bounded traffic, realistic user_id values):
```bash
./load.sh
```

Watch the series count.  With the `load.sh` script, user IDs cycle through a small set — the explosion is slow.

---

### Challenge 2 — Trigger the cardinality explosion

Stop `load.sh` (Ctrl+C) and run the cardinality load generator:

```bash
./cardinality.sh 10
```

This sends requests with a unique random user_id on every request — simulating a realistic high-user-count production environment.

Watch the **Cardinality Monitor** dashboard.  You should see:

1. **Total Active Series** — rising continuously, ~60+ new series per minute
2. **Series Growth Rate** — showing > 50/min (triggering the `CardinalityExplosion` alert)
3. **Prometheus Memory** — RSS growing alongside the series count
4. **Scrape Samples per Target** — the lumio-api scrape is returning more samples each cycle

Within 2 minutes, the `CardinalityExplosion` alert should fire in the webhook logs:
```bash
docker compose logs -f webhook
```

Also observe `scrape_duration_seconds` — as the series count grows, the time Prometheus spends scraping the `/metrics` endpoint also grows.

**Let the explosion run for 5–10 minutes** before moving to Challenge 3 so the signal is clear in the dashboard.

---

### Challenge 3 — Diagnose the offending label

Press Ctrl+C to stop `cardinality.sh`.

#### Step 1: TSDB Status page

Open the Prometheus TSDB status page: http://localhost:9090/tsdb-status

Look at the **Top 10 series count by metric name** table.  `lumio_http_requests_total` should be near the top with many more series than the other metrics.

Look at **Top 10 series count by label name**.  `user_id` should appear with a count close to the total `lumio_http_requests_total` series count — confirming it as the source.

#### Step 2: Confirm with PromQL

```bash
# How many series does lumio_http_requests_total currently have?
curl -sg 'http://localhost:9090/api/v1/query?query=count(lumio_http_requests_total)' \
  | python3 -m json.tool

# Compare with the other metrics from the same job
curl -sg 'http://localhost:9090/api/v1/query?query=count+by+(__name__)({job="lumio-api"})' \
  | python3 -m json.tool
```

The `count()` result for `lumio_http_requests_total` should be several hundred or thousand times higher than `lumio_http_request_duration_seconds_count`.

#### Step 3: Inspect the label values

```bash
# How many unique user_id values are in the metric right now?
curl -sg 'http://localhost:9090/api/v1/label/user_id/values' \
  | python3 -c "import sys,json; v=json.load(sys.stdin)['data']; print(f'{len(v)} unique user_id values')"
```

#### Step 4: Understand why this wasn't caught earlier

The app's `REQUEST_COUNT` Counter has four labels: `method`, `endpoint`, `status_code`, `user_id`.  

- `method`: ~2 values
- `endpoint`: ~5 values
- `status_code`: ~3 values
- `user_id`: **unbounded**

With only a few users in development, `user_id` seemed harmless — maybe 10–20 series.  In production with 50 000 users it becomes 300 000+ series.  This is the development-to-production cardinality trap.

---

### Challenge 4 — Apply the hotfix (metric_relabel_configs)

The cardinality explosion is in progress.  Prometheus is consuming too much memory.  You need to stop the bleeding without restarting the application.

Open `prometheus/prometheus.yml` and uncomment the `metric_relabel_configs` block:

```yaml
metric_relabel_configs:
  - regex: user_id
    action: labeldrop
```

Save the file and reload Prometheus **without restart**:

```bash
curl -X POST http://localhost:9090/-/reload
```

Watch the **Cardinality Monitor** dashboard:
- **Series Growth Rate** should immediately drop to near 0 (new scrapes no longer create new series)
- **Total Active Series** will NOT drop immediately — existing series remain until they go stale (no new sample for 5+ minutes) and are garbage-collected
- Within 5–10 minutes the series count should start falling as stale series expire

Verify the hotfix worked by checking that `user_id` no longer appears as a label on new samples:

```bash
curl -sg 'http://localhost:9090/api/v1/query?query=lumio_http_requests_total' \
  | python3 -c "
import sys, json
r = json.load(sys.stdin)
series = r['data']['result']
labels_with_user_id = [s for s in series if 'user_id' in s['metric']]
print(f'Series with user_id label: {len(labels_with_user_id)}')
print(f'Total series: {len(series)}')
"
```

**Key insight:** The app is still sending user_id in its `/metrics` output.  `metric_relabel_configs` drops it at the Prometheus ingestion boundary.  This is a hotfix — it stops new cardinality from accumulating but doesn't fix the application.

---

### Challenge 5 — Observe the recovery

With the hotfix in place, watch the **Total Active Series** timeseries panel over the next 10–15 minutes.

The series count will decline in steps:
1. **Immediately**: the growth rate drops to 0 (new scrapes no longer create new user_id series)
2. **After ~5 minutes**: the existing user_id series become stale (no new samples)
3. **After ~10 minutes**: stale series start being removed from the head block
4. **After ~2 hours**: the TSDB head compaction removes all stale series

You won't want to wait 2 hours in the lab.  Observe the first two phases and understand the pattern.

Also check the Prometheus alerts page: http://localhost:9090/alerts

The `CardinalityExplosion` alert should now be in `pending` or `resolved` state as the growth rate has dropped.

---

### Challenge 6 — Fix it properly in the application

The hotfix stops new cardinality from accumulating, but the application is still generating user_id label values.  Every scrape, the app enumerates thousands of user_id series — then Prometheus throws them away.  This wastes CPU and network on every scrape.

The correct fix: remove `user_id` from the metric label set and move per-user data to structured logs.

**Step 1:** View the fixed application code:

```bash
cat api/app_fixed.py
```

The key change in `app_fixed.py`:

```python
# Before (buggy):
REQUEST_COUNT = Counter(
    "lumio_http_requests_total",
    "...",
    ["method", "endpoint", "status_code", "user_id"],  # ← unbounded
)

# After (fixed):
REQUEST_COUNT = Counter(
    "lumio_http_requests_total",
    "...",
    ["method", "endpoint", "status_code"],  # ← bounded
)
```

And per-user data moved to logs:

```python
# The user_id is preserved in structured log lines — queryable via Loki
logger.info("event accepted", extra={"event_type": event_type, "user_id": user_id})
```

**Step 2:** Apply the fix:

```bash
cp api/app_fixed.py api/app.py
docker compose up --build -d api
```

**Step 3:** Verify the fix:

```bash
# The /metrics output should no longer contain user_id
curl -s http://localhost:8000/metrics | grep lumio_http_requests_total | head -5

# Series count for lumio_http_requests_total should be small and bounded
curl -sg 'http://localhost:9090/api/v1/query?query=count(lumio_http_requests_total)' \
  | python3 -m json.tool
```

With the fix in place, `count(lumio_http_requests_total)` should return a number close to `5 endpoints × 2 methods × 3 status codes = 30`, regardless of how many users are active.

**Step 4:** Remove the hotfix (now unnecessary):

Open `prometheus/prometheus.yml` and comment out the `metric_relabel_configs` block again.  Reload:

```bash
curl -X POST http://localhost:9090/-/reload
```

---

### Challenge 7 — Other cardinality pitfalls

**Pitfall A: URL paths with embedded IDs**

What if instead of `user_id` as a label, the developer had used `request.path` as the endpoint label?

Try it in the Prometheus UI (no code change needed):

```promql
# What does the endpoint label look like in this app?
count by(endpoint)(lumio_http_requests_total)
```

This app uses Flask's `request.endpoint` (the route function name), which is bounded.  If it used `request.path` instead, endpoints like `/api/users/12345/orders/67890` would create a new series per unique user and order ID.

**Pitfall B: Histogram bucket count**

Run this query to see how many series the histogram creates:

```promql
count({__name__=~"lumio_http_request_duration_seconds.*"})
```

With 10 bucket boundaries plus `_count` and `_sum`, that's 12 series per (method, endpoint) combination.  If you doubled the bucket count to 20, you'd double the series count.

Check the current bucket definition in `api/app.py`:
```python
buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
```

10 buckets is a good default.  Avoid custom per-percentile buckets (e.g. 100 buckets from 0.001 to 10.0) — they multiply series count with no observational benefit over 10 well-placed buckets.

**Pitfall C: Staleness and churn**

Run `cardinality.sh` for 30 seconds, then stop it.  Look at:

```promql
prometheus_tsdb_head_series
```

The series count stays high even after the load stops — stale series aren't removed immediately.  They persist for 5 minutes of inactivity (the staleness window) plus TSDB head compaction time.

This means cardinality explosions have a delayed recovery.  If Prometheus is approaching its memory limit, you cannot wait for natural expiry — you must either restart (losing the last 2h of head data) or apply `metric_relabel_configs` to stop accumulation.

---

### Challenge 8 — Write a cardinality explosion alert

The `lumio_alerting.yml` file already contains two cardinality alerts.  Let's understand them:

```bash
cat prometheus/rules/lumio_alerting.yml | grep -A 20 "cardinality.alerting" 
```

Open the Prometheus alerts page: http://localhost:9090/alerts

The `PrometheusHighCardinality` alert is a **threshold alert** — fires when the absolute count is too high.  The `CardinalityExplosion` alert is a **rate alert** — fires when new series are being created rapidly.

**Tune the thresholds for your baseline:**

The current thresholds (`> 10000` for count, `> 50/min` for rate) are designed for this lab.  In production you'd calculate them from your normal baseline:

```promql
# What's the 95th percentile of your series count over the last 7 days?
quantile_over_time(0.95, prometheus_tsdb_head_series[7d])
```

Set your warning threshold at ~2× the 95th percentile and the critical at ~5×.

**Test the alert:**

```bash
./cardinality.sh 20   # higher concurrency for a faster test
```

Watch the webhook logs for the `CardinalityExplosion` alert to fire:

```bash
docker compose logs -f webhook
```

Then stop the script and verify the alert resolves within a few minutes of the growth rate returning to 0.

---

## File structure

```
phase-8-cardinality/
└── app/
    ├── docker-compose.yml
    ├── load.sh                         ← bounded normal traffic
    ├── break.sh                        ← triggers high error rate
    ├── cardinality.sh                  ← unique X-User-ID per request (triggers explosion)
    ├── api/
    │   ├── app.py                      ← BUGGY: user_id in metric labels
    │   ├── app_fixed.py                ← FIXED: user_id in structured logs only
    │   ├── Dockerfile
    │   └── requirements.txt
    ├── prometheus/
    │   ├── prometheus.yml              ← hotfix commented out (uncomment in Challenge 4)
    │   └── rules/
    │       ├── lumio_recording.yml     ← Phase 6 recording rules
    │       └── lumio_alerting.yml      ← app alerts + PrometheusHighCardinality + CardinalityExplosion
    ├── alertmanager/
    │   └── alertmanager.yml
    ├── webhook/
    └── grafana/
        ├── provisioning/
        └── dashboards/
            ├── lumio-api.json          ← API dashboard (with inline series count stat)
            └── lumio-cardinality.json  ← TSDB health: series count, growth rate, memory, scrape samples
```

---

## Command reference

```bash
# Start
docker compose up --build -d

# Normal bounded traffic
./load.sh

# Trigger cardinality explosion (unique user_id per request)
./cardinality.sh         # 10 workers
./cardinality.sh 25      # 25 workers (faster)

# Check series count
curl -sg 'http://localhost:9090/api/v1/query?query=prometheus_tsdb_head_series' \
  | python3 -m json.tool

# Count series per metric name (expensive at high cardinality — use tsdb-status instead)
curl -sg 'http://localhost:9090/api/v1/query?query=count+by(__name__)({job="lumio-api"})' \
  | python3 -m json.tool

# Count unique user_id values
curl -sg 'http://localhost:9090/api/v1/label/user_id/values' \
  | python3 -c "import sys,json; v=json.load(sys.stdin)['data']; print(len(v), 'unique values')"

# Apply hotfix and reload (after uncommenting metric_relabel_configs in prometheus.yml)
curl -X POST http://localhost:9090/-/reload

# Apply code fix
cp api/app_fixed.py api/app.py
docker compose up --build -d api

# Stop
docker compose down -v
```

---

## What this doesn't do yet

| Gap | Next phase |
|---|---|
| Same alerts fire in dev and prod — on-call tunes out the noise | Phase 9 — Multi-environment Observability |
| You can't tell whether a cardinality alert is from dev or prod | Phase 9 — adds `env` label to all scrape targets |

---

## Production considerations

**Set memory limits and `--storage.tsdb.max-block-chunk-seg-size` on Prometheus.**  Without a memory limit, a cardinality explosion OOMs the host.  With a limit, Prometheus is OOM-killed but Docker/Kubernetes restarts it quickly — a degraded-but-recovering state rather than a complete outage.

**Monitor `prometheus_tsdb_head_series` from day one.**  Add the `CardinalityExplosion` and `PrometheusHighCardinality` alerts before you have any cardinality problems.  These alerts need time to establish a stable baseline; adding them after an explosion is already in progress is too late.

**Use `metric_relabel_configs` in code review.**  When a team proposes a new label, require a cardinality analysis: "what is the maximum number of unique values this label can take?"  If the answer is "depends on user behaviour," reject it.

**The TSDB status page is your incident runbook.** Bookmark http://your-prometheus:9090/tsdb-status.  In a cardinality incident, it tells you in seconds which metric and which label are responsible.

**Structured logs are the right tool for per-entity data.**  Metrics aggregate.  Logs enumerate.  "How many requests did user_42 make?" is a log query (Loki: `{service="api"} | json | user_id = "user_42" | count_over_time([1h])`).  "What is the 95th-percentile latency across all users?" is a metric query.
