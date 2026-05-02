# Phase 6 — Recording Rules & Query Optimisation

> **Lumio, 40 engineers.**
> The Lumio dashboard has become the team's morning ritual — everyone opens it over coffee to check overnight traffic.  Then one Monday the on-call engineer notices it takes 30 seconds to load.  Prometheus CPU is pegged.  The cause: eight panels each running `histogram_quantile(0.95, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))` — the single most expensive PromQL expression in the codebase — simultaneously, for every person who has a browser tab open.

## What you will build

A lean stack — API, Prometheus, Alertmanager, webhook, Grafana — with two side-by-side dashboards:

| Dashboard | Approach | Panel query |
|---|---|---|
| **Before** (`/d/lumio-before`) | Raw PromQL | `histogram_quantile(0.95, sum by(le)(rate(...[5m])))` |
| **After** (`/d/lumio-after`) | Recording rules | `job:lumio_http_request_duration_seconds:p95rate5m` |

Both dashboards show identical data.  The difference is entirely in _where_ the computation happens: at query time (every refresh, every viewer) vs at evaluation time (once every 15s, regardless of viewers).

---

## Concepts

### Why PromQL queries get expensive

Prometheus stores raw counter and histogram values.  Every time a panel refreshes it:

1. Finds all matching time series (a label scan)
2. Reads the raw samples for each series over the query range
3. Applies the function (`rate()`, `histogram_quantile()`, etc.)
4. Aggregates the result

For a histogram with 11 default `le` buckets across 6 endpoints, step 3 alone touches 66 bucket series — and it repeats for every `rate()` call inside the `histogram_quantile()`.

With 10 engineers each with a tab open and a 15-second auto-refresh, that is 10 × 4 panels × 4 evaluations/minute = **160 histogram_quantile evaluations per minute**, each touching 66 series.

### What recording rules do

A recording rule is a PromQL expression that Prometheus evaluates on a fixed interval and stores the result as a new metric.  Instead of re-running the expression at query time you just look up the stored series.

```
# Without recording rules — evaluated on EVERY query
histogram_quantile(0.95, sum by(le, job)(rate(lumio_http_request_duration_seconds_bucket[5m])))

# With recording rules — evaluated ONCE per 15s, stored as:
job:lumio_http_request_duration_seconds:p95rate5m
```

The stored metric is a Gauge-type time series.  Reading it at query time is a label index lookup — O(1), regardless of how many users are viewing the dashboard.

### Naming convention: `<level>:<metric_name>:<aggregation>`

Prometheus recording rules don't enforce naming, but the community convention is:

```
<level>:<metric_name>:<aggregation>
```

| Part | Meaning | Example |
|---|---|---|
| `level` | The label(s) the result is grouped by | `job`, `job_endpoint` |
| `metric_name` | The base metric being summarised | `lumio_http_requests_total` |
| `aggregation` | The operation and window | `rate5m`, `p95rate5m`, `ratio5m` |

This makes metric names self-documenting:
- `job:lumio_http_requests_total:rate5m` → per-job request rate, 5-minute window
- `job_endpoint:lumio_http_request_duration_seconds:p95rate5m` → per-job-and-endpoint P95, 5m window

You can grep for all pre-computed rates across a running Prometheus instance:
```
promtool query instant http://localhost:9090 '{__name__=~"job.*:rate5m"}'
```

### When recording rules help — and when they don't

**Do use recording rules for:**
- `histogram_quantile()` — always expensive; always record it
- Ratios and fractions (`rate(errors) / rate(total)`) used by alerts or dashboards
- High-cardinality aggregations used across multiple dashboards or alert rules
- Any expression you find yourself copy-pasting into multiple places

**Don't use recording rules for:**
- Panels with template variables (`$endpoint`) — the recording rule pre-computes for _all_ label values; a variable filter happens at query time and cannot be pushed into the stored metric
- One-off exploration queries in Explore
- Metrics that change infrequently — the overhead of evaluating a recording rule every 15s for a metric that barely changes is rarely worth it

### Rule file load order

Prometheus evaluates rule files in the order they appear in `rule_files`.  If an alerting rule references a recorded metric, the recording rule file **must be listed first**:

```yaml
rule_files:
  - /etc/prometheus/rules/lumio_recording.yml   # ← evaluated first
  - /etc/prometheus/rules/lumio_alerting.yml    # ← can reference recorded metrics
```

If you get this wrong Prometheus won't fail — it will silently produce `no data` for alerts that reference not-yet-evaluated recording rules, and fire `LumioServiceDown` unexpectedly.

---

## Stack

```
┌──────────────────────────────────────────────────────┐
│  docker compose up                                   │
│                                                      │
│  api:8000        ←── load.sh                         │
│  prometheus:9090 ←── scrapes api + itself            │
│  alertmanager:9093                                   │
│  webhook:5001    ←── alertmanager → webhook          │
│  grafana:3000    ←── two dashboards: before / after  │
└──────────────────────────────────────────────────────┘
```

Prometheus is configured to scrape itself (`job_name: prometheus`) so you can observe rule evaluation duration and series count in the "after" dashboard (Challenge 8).

---

## Challenges

### Challenge 1 — Start the stack

```bash
cd phase-6-recording-rules/app
docker compose up --build -d
```

Verify:
- API health: `curl http://localhost:8000/health`
- Prometheus targets: http://localhost:9090/targets — both `lumio-api` and `prometheus` should show `UP`
- Grafana: http://localhost:3000 (admin / lumio) — two dashboards should be provisioned

Start a load generator to produce realistic traffic:
```bash
./load.sh
```

---

### Challenge 2 — Observe the "before" dashboard

Open the **Lumio API — Before (raw PromQL)** dashboard: http://localhost:3000/d/lumio-before

Every panel description shows the raw PromQL it evaluates.  With auto-refresh set to 15s, click each panel's title and choose **Explore** to see the query.  Note:
- The P95 Latency panel runs `histogram_quantile(0.95, sum by(le, job)(rate(lumio_http_request_duration_seconds_bucket[5m])))` on every refresh
- The P95 by Endpoint panel adds an `endpoint` dimension, making the bucket scan even wider
- The three percentile panels (P50/P95/P99) together run three separate bucket scans on the same data

**Question:** If 10 team members have this dashboard open with 15s auto-refresh, how many `histogram_quantile` evaluations happen per minute?

> Answer: 10 users × 4 histogram panels × 4 refreshes/min = 160 evaluations/min, each scanning all bucket series.

---

### Challenge 3 — Understand the recording rules file

Open `prometheus/rules/lumio_recording.yml`.  Nine recording rules are defined.  For each, the file explains:
1. The raw PromQL it replaces
2. Why that expression is expensive
3. Why pre-computing it once per 15s is better

Study the naming convention. Then in the Prometheus UI, go to **Status → Rules** (http://localhost:9090/rules) and confirm the rule group `lumio.api.recording` is listed with all nine rules in state `ok`.

Run one directly:
```bash
curl -sg 'http://localhost:9090/api/v1/query?query=job:lumio_http_request_duration_seconds:p95rate5m' \
  | python3 -m json.tool
```

You should see a result like:
```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [{ "metric": { "__name__": "job:lumio_http_request_duration_seconds:p95rate5m", "job": "lumio-api" }, "value": [...] }]
  }
}
```

---

### Challenge 4 — Verify all recorded metrics exist

Use `promtool` (bundled inside the Prometheus container) to query all pre-computed rate metrics at once:

```bash
docker compose exec prometheus \
  promtool query instant http://localhost:9090 \
  '{__name__=~"job.*:rate5m|job.*:p.*rate5m|job.*:ratio5m"}'
```

You should see all nine recorded metrics in the output.

Alternatively, in the Prometheus UI use the metric explorer and type `job:` — all recorded job-level metrics will appear in the autocomplete.

---

### Challenge 5 — Switch to the "after" dashboard

Open **Lumio API — After (recording rules)**: http://localhost:3000/d/lumio-after

Every panel description shows the recorded metric it reads.  Open each panel in Explore and confirm:
- The query is a simple label matcher — no `rate()`, no `histogram_quantile()`
- The result is identical to the "before" dashboard

Use the top link to flip between the two dashboards and compare.

**Key insight:** The data is the same.  The query cost is not.

---

### Challenge 6 — Rewrite the alerting rules to use recorded metrics

Open `prometheus/rules/lumio_alerting.yml`.  The alerting rules already reference recorded metrics:

```yaml
# Before recording rules:
expr: >
  sum(rate(lumio_http_requests_total{status_code=~"5.."}[5m])) by (job)
  /
  sum(rate(lumio_http_requests_total[5m])) by (job)
  > 0.05

# After — reads the pre-computed ratio:
expr: >
  job:lumio_http_requests_error_ratio:rate5m{job="lumio-api"} > 0.05
```

This matters: Prometheus evaluates alerting rules on every `evaluation_interval`.  If your alerting rule re-runs an expensive `histogram_quantile` expression every 15s, the optimisation benefit of recording rules is partially lost.

Trigger an incident to verify the alerts still fire correctly:
```bash
./break.sh 0.5   # set 50% error rate
```

Watch the webhook receiver logs:
```bash
docker compose logs -f webhook
```

Within 2 minutes you should see `LumioHighErrorRate` at severity `critical`.  Reset:
```bash
./break.sh 0.0
```

---

### Challenge 7 — When recording rules don't help: template variables

Recording rules pre-compute for **all** label values present at evaluation time.  They cannot filter for a specific label value that is only known at query time.

To demonstrate this, try adding a template variable to the "after" dashboard:

1. In Grafana, go to **Dashboard settings → Variables → Add variable**
2. Type: `Query`, Name: `endpoint`
3. Query: `label_values(job_endpoint:lumio_http_requests_total:rate5m, endpoint)`

Now change the panel query to:
```
job_endpoint:lumio_http_requests_total:rate5m{job="lumio-api", endpoint="$endpoint"}
```

The `$endpoint` filter is applied at query time, after Prometheus returns the full recorded series.  The recording rule still saves you the `rate()` evaluation, but the label filter cannot be pre-computed.

**Implication:** recording rules are most valuable when you know the exact label set at write time.  If your dashboard has template variables that filter to a single label value, the benefit is reduced (though still present for histograms).

---

### Challenge 8 — Monitor Prometheus itself

The "after" dashboard includes two panels at the bottom that show Prometheus's internal health.

**Panel: Prometheus Rule Evaluation Duration**

```promql
histogram_quantile(0.99,
  rate(prometheus_engine_query_duration_seconds_bucket{slice="inner_eval"}[5m])
)
```

This measures how long the rule evaluation engine takes.  A healthy value for this lab is under 10ms.  If you add a badly written recording rule (e.g. a high-cardinality regex match across millions of series), this will spike.

**Panel: Active Time Series (TSDB Head)**

```promql
prometheus_tsdb_head_series
```

Every recording rule adds series to the TSDB head.  In this lab, the nine recording rules add roughly 30–50 new series (depending on how many endpoints and event types are active).  Watch this number before and after adding rules to understand the storage cost.

**Panel: Rule Group Evaluation Interval**

```promql
prometheus_rule_group_last_duration_seconds
```

This is how long the last evaluation of each rule group took.  If this value approaches your `evaluation_interval` (15s), Prometheus cannot keep up — evaluations start queuing and you lose freshness.

> **Production note:** In large environments, split rule groups by evaluation cost.  Fast, cheap alerting rules should be in a group with a short `interval`.  Expensive recording rules can use a longer `interval` (e.g. 30s or 1m) if dashboard freshness isn't critical.

---

## File structure

```
phase-6-recording-rules/
└── app/
    ├── docker-compose.yml
    ├── load.sh
    ├── break.sh
    ├── api/
    │   ├── Dockerfile
    │   ├── app.py
    │   └── requirements.txt
    ├── prometheus/
    │   ├── prometheus.yml              ← scrapes api + prometheus itself
    │   └── rules/
    │       ├── lumio_recording.yml     ← 9 recording rules (loaded first)
    │       └── lumio_alerting.yml      ← alerting rules using recorded metrics
    ├── alertmanager/
    │   └── alertmanager.yml
    ├── webhook/
    │   ├── Dockerfile
    │   ├── webhook.py
    │   └── requirements.txt
    └── grafana/
        ├── provisioning/
        │   ├── datasources/
        │   │   └── prometheus.yml
        │   └── dashboards/
        │       └── lumio.yml
        └── dashboards/
            ├── lumio-before.json       ← raw PromQL (the problem)
            └── lumio-after.json        ← recording rules (the fix)
```

---

## Command reference

```bash
# Start
docker compose up --build -d

# Generate traffic
./load.sh

# Trigger high error rate
./break.sh 0.5

# Reset error rate
./break.sh 0.0

# Query a recorded metric directly
curl -sg 'http://localhost:9090/api/v1/query?query=job:lumio_http_request_duration_seconds:p95rate5m' \
  | python3 -m json.tool

# Check all recording rule states
curl -sg http://localhost:9090/api/v1/rules | python3 -m json.tool | grep '"name"'

# Reload Prometheus config (without restart)
curl -X POST http://localhost:9090/-/reload

# Stop
docker compose down -v
```

---

## What this doesn't do yet

| Gap | Next phase |
|---|---|
| Dashboard loads are fast but **host disk** is nearly full | Phase 7 — node_exporter + infrastructure metrics |
| Prometheus itself OOM-killed by a high-cardinality label | Phase 8 — Cardinality & Production Pitfalls |
| Same alerts fire in dev and prod; on-call is tuning out | Phase 9 — Multi-environment observability |

---

## Production considerations

**Don't record everything.** Every recording rule is a new time series persisted to disk.  Record expensive expressions that appear in multiple places (dashboards, alerting rules).  Don't record one-off exploration queries.

**Match your recording interval to your scrape interval.** If `scrape_interval` is 15s and `evaluation_interval` is 60s, your recorded metric will be 45s stale when a dashboard opens.  Set `evaluation_interval: 15s` (the default) to match.

**Use the three-part naming convention consistently.** It makes autocomplete useful and lets you `grep` for all pre-computed series of a given type.

**Check `prometheus_rule_group_last_duration_seconds` in production.** If it exceeds your `evaluation_interval`, your alerting rules are evaluating on stale data.  Split groups or increase the interval for expensive rules.

**Recording rules don't survive a cold Prometheus restart with empty storage.** The first evaluation cycle after restart re-populates recorded metrics.  With `evaluation_interval: 15s` the gap is 15s — usually acceptable.  If you need zero-gap continuity, use Prometheus remote write with a durable backend.
