# Phase 4 — Log Aggregation with Loki

> **Concepts introduced:** Structured logging, JSON log format, Loki architecture, log streams, log labels vs log content, Promtail, Docker service discovery, LogQL, label cardinality in Loki, log-to-metric correlation

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Structured logging** | Logs emitted as JSON objects instead of free text | Fields are queryable — find all logs where `event_type=checkout` without regex |
| **Log stream** | A sequence of log lines sharing the same set of labels | The unit of storage in Loki — same label set = same stream |
| **Loki** | Log aggregation system that indexes labels, not content | Stores log lines cheaply; queries by label first, then filters content |
| **Promtail** | An agent that scrapes log files or Docker stdout and pushes to Loki | The bridge between container logs and Loki |
| **Docker SD** | Promtail's Docker service discovery — automatically finds containers | No manual log file paths needed; works with any container |
| **Pipeline stages** | Promtail processing steps: parse JSON, extract labels, drop lines | Transforms raw log lines before sending to Loki |
| **LogQL** | Loki's query language — log selectors + filter/metric expressions | Select streams by label, filter by content, count or rate log lines |
| **Label selector** | `{service="api", level="WARNING"}` — selects a stream | Fast — Loki indexes labels; all subsequent filters scan only matching streams |
| **Filter expression** | `|= "checkout"` or `| json | event_type = "checkout"` — content search | Scans log lines in selected streams; slower than label matching |
| **Log metric** | `rate({service="api"}[1m])` — converts log volume to a rate | Enables alerting on log line rate or content patterns |
| **Label cardinality** | The number of unique label value combinations in Loki | Same problem as Prometheus: high-cardinality labels (request IDs, user IDs) explode stream count |

---

## The problem

> *Lumio — 25 engineers. Four weeks after Phase 2.*
>
> The alerting was working. The error rate alert fired and paged the oncall. They opened Grafana: error rate at 8%. The P95 latency was fine. The service was up.
>
> "Something is wrong with event processing. Which event type? What error?"
>
> The only way to find out was to SSH into the container and run `docker logs`. The logs were there — raw text, one line per event, scattered across however many container restarts had happened. By the time they found the relevant lines, 20 minutes had passed.
>
> The fix: centralise the logs in Loki, make them structured, and surface them in the same Grafana interface as the metrics. The next incident took 4 minutes to diagnose instead of 20: open the error rate alert, switch to the Logs panel in the same dashboard, filter to `level=WARNING`, read the structured `error_reason` field directly.

---

## Architecture

```
phase-4-loki/app/

  ┌─────────────┐ stdout (JSON)  ┌──────────────┐  push   ┌────────┐
  │  lumio-api  │───────────────►│   Promtail   │────────►│  Loki  │
  │  :8000      │                │  (Docker SD) │         │  :3100 │
  └──────┬──────┘                └──────────────┘         └───┬────┘
         │ /metrics                                            │
         ▼                                                     │
  ┌─────────────────┐                                         │
  │   Prometheus    │                                         │
  │   :9090         │         ┌──────────────────────────────┐│
  └────────┬────────┘         │  Grafana  :3000              ││
           │                  │                              ││
           │ PromQL ──────────┤  Prometheus datasource       ││
           │                  │  Loki datasource  ───────────┘│
           │ alerts ──────────►  (Logs + Metrics dashboard)   │
  ┌────────▼────────┐         └──────────────────────────────┘
  │  Alertmanager   │
  │  :9093          │
  └────────┬────────┘
           │
  ┌────────▼────────┐
  │  webhook :5001  │
  └─────────────────┘
```

Logs and metrics live in separate systems (Loki and Prometheus) but are surfaced together in Grafana. Promtail is the bridge — it reads Docker container stdout, parses the JSON, and ships to Loki.

---

## Repository structure

```
phase-4-loki/
└── app/
    ├── docker-compose.yml
    ├── load.sh
    ├── break.sh
    ├── api/
    │   ├── app.py              ← adds JSON structured logging
    │   ├── Dockerfile
    │   └── requirements.txt
    ├── prometheus/
    │   ├── prometheus.yml
    │   └── rules/lumio.yml
    ├── alertmanager/
    │   └── alertmanager.yml
    ├── webhook/
    ├── loki/
    │   └── loki.yml
    ├── promtail/
    │   └── promtail.yml        ← Docker SD + JSON pipeline stages
    └── grafana/
        ├── provisioning/
        │   ├── datasources/
        │   │   ├── prometheus.yml
        │   │   └── loki.yml
        │   └── dashboards/lumio.yml
        └── dashboards/
            └── lumio-logs.json ← combined metrics + logs dashboard
```

---

## Challenge 1 — Start the stack

```bash
cd phase-4-loki/app
docker compose up -d --build
```

Seven containers start: `api`, `prometheus`, `alertmanager`, `webhook`, `loki`, `promtail`, `grafana`.

```bash
docker compose ps
```

### Verify Loki is ready

```bash
curl -s http://localhost:3100/ready
```

Should return `ready`.

### Verify Promtail is scraping

```bash
curl -s http://localhost:9080/targets | grep -o 'service="[^"]*"'
```

You should see `service="api"` in the output, confirming Promtail has discovered the API container.

### Generate load

```bash
chmod +x load.sh && ./load.sh
```

Wait ~30 seconds, then open **http://localhost:3000** → Dashboards → Lumio → **Lumio API — Logs + Metrics**.

The bottom half of the dashboard shows live log lines from the API alongside the metrics panels at the top.

---

## Challenge 2 — Understand structured logging in app.py

Open `api/app.py`. Find the `JSONFormatter` class.

### Raw text logging (before Phase 4)

```
2025-01-15 14:23:01 WARNING event processing failed type=checkout reason=timeout
```

This line is queryable only with regex: `|~ "type=checkout"`. But regex matching is:
- Slow — Loki must scan every matching log line
- Fragile — changes to the log format break queries
- Context-free — you cannot filter by timestamp range + event type + error reason in a single efficient query

### Structured JSON logging (Phase 4)

```json
{"timestamp":"2025-01-15T14:23:01.123Z","level":"WARNING","logger":"lumio",
 "message":"event processing failed","event_type":"checkout","error_reason":"timeout","duration_ms":47.2}
```

The same information is now:
- Queryable by field: `| json | error_reason = "timeout"`
- Labelable: Promtail promotes `level` and `event_type` to Loki labels
- Extensible: new fields can be added without breaking existing queries

### How extra fields are attached

```python
logger.warning(
    "event processing failed",
    extra={
        "event_type":   event_type,
        "error_reason": reason,
        "duration_ms":  duration_ms,
    },
)
```

The `extra` dict is merged into the `LogRecord` by Python's logging module. The `JSONFormatter` then iterates the record's attributes and includes any non-standard fields in the JSON output. This pattern keeps the logger call ergonomic while producing structured output.

---

## Challenge 3 — Understand the Promtail pipeline

Open `promtail/promtail.yml`. The `pipeline_stages` section transforms raw log lines before shipping to Loki.

```yaml
pipeline_stages:
  - json:
      expressions:
        level:        level
        event_type:   event_type
        error_reason: error_reason

  - labels:
      level:
      event_type:

  - drop:
      expression: '.*"level":"DEBUG".*'
```

### Stage 1: json

Parses the JSON log line and extracts named fields into Promtail's internal pipeline state. The key is the variable name in the pipeline, the value is the JSON field name. After this stage, `level`, `event_type`, and `error_reason` are available as extracted values.

### Stage 2: labels

Promotes extracted values to Loki labels. Only `level` and `event_type` become labels — `error_reason` is extracted but stays as log content (not a label).

**Why not promote everything?** Label cardinality. If `error_reason` had 1,000 possible values, promoting it to a label would create 1,000 Loki streams for the `api` service. Loki's recommendation: promote only fields with a small, bounded set of values (log levels, event types, HTTP methods) to labels. Keep high-cardinality values (request IDs, user IDs, error messages) in the log content, queryable with `| json`.

### Stage 3: drop

Drops lines matching the regex before they reach Loki. Removes DEBUG lines to reduce storage volume. Comment this stage out to keep all levels.

### The label set for a WARNING log from the API

After the pipeline, each log line sent to Loki has these labels:

```
{
  service    = "api",       ← from Docker Compose service name (relabelling)
  container  = "app-api-1", ← from Docker container name
  stream     = "stdout",
  level      = "WARNING",   ← promoted from JSON
  event_type = "checkout"   ← promoted from JSON (or absent for non-event logs)
}
```

---

## Challenge 4 — Your first LogQL queries

Open Grafana Explore (compass icon) → select **Loki** datasource.

### Log selector — stream selection

```
{service="api"}
```

Returns all log lines from the `api` service. This is a label selector — Loki looks up which streams match and returns their lines. Fast.

### Filter by level

```
{service="api", level="WARNING"}
```

Returns only warning-level logs. Since `level` is a label, this is stream selection — no content scanning needed.

### Filter by content

```
{service="api"} |= "checkout"
```

Returns lines containing the string "checkout". Scans all lines in matching streams. Slower than label selection but flexible.

### Parse JSON and filter by field

```
{service="api"} | json | error_reason = "timeout"
```

Parses each line as JSON, then filters to lines where `error_reason` is `"timeout"`. This is more precise than a string match and works even if "timeout" appears elsewhere in the log line.

### Count errors per minute by reason

```
sum(rate({service="api", level="WARNING"} | json | unwrap duration_ms [1m])) by (error_reason)
```

Or simpler — just count lines:

```
sum(rate({service="api", level="WARNING"}[1m])) by (error_reason)
```

This is a metric query derived from log data. It returns a time series, just like PromQL — log volume converted to a rate. You can use this in a panel or alert on it.

---

## Challenge 5 — Trigger errors and find them in logs

### Step 1: Start load and break it

```bash
chmod +x break.sh && ./break.sh
```

### Step 2: Find the error logs in Grafana

Open the **Lumio API — Logs + Metrics** dashboard.

Watch the **Error rate** stat panel turn yellow/red. Below it, the **Error logs** panel (filtered to `level="WARNING"`) shows individual error log lines in real time.

Click on a log line to expand its details. You will see the structured fields: `event_type`, `error_reason`, `duration_ms`.

This is the workflow that was impossible before Phase 4:
1. Alert fires → oncall opens Grafana
2. Error rate panel confirms the rate
3. Error logs panel shows the specific failures
4. `error_reason` field identifies the root cause without SSHing

### Step 3: Correlate log spike with metric spike

In Grafana Explore, run the log volume metric query:

```
sum(rate({service="api"}[1m])) by (level)
```

Switch to the **Graph** view. The WARNING line spikes when errors increase. Switch the time range to match when you ran `break.sh`.

Now run the Prometheus query in a second Explore panel:

```
sum(rate(lumio_http_requests_total{status_code=~"5.."}[5m]))
```

Both spikes should align to the same time window. Loki and Prometheus are both observing the same incident from different angles: one counting HTTP 5xx responses, the other counting log lines at WARNING level.

---

## Challenge 6 — LogQL: count slow events by type

The `duration_ms` field in the log tells you how long each event took to process. You can use this as a log-derived metric.

### Step 1: Find slow events

```
{service="api"} | json | duration_ms > 45
```

Returns only log lines where the processing took more than 45ms. Useful for finding tail latency events that the P95 metric aggregates away.

### Step 2: Count slow events per event type

```
sum(count_over_time(
  {service="api"} | json | duration_ms > 45 [5m]
)) by (event_type)
```

This counts log lines in 5-minute windows where `duration_ms > 45`, grouped by `event_type`. It answers: "which event type has the most slow processing?"

You cannot answer this question with the Prometheus metrics from Phase 0–3 — the `event_type` label is not on the latency histogram. Logs fill that gap: the structured `event_type` and `duration_ms` fields in every log line let you derive per-type latency distributions without adding high-cardinality labels to your metrics.

---

## Challenge 7 — Understand Loki's storage model

Loki stores data differently from Prometheus and is deliberately limited in what it indexes.

### What Loki indexes

**Only labels.** The label set `{service="api", level="WARNING"}` is indexed. Querying by these labels is fast regardless of log volume.

### What Loki does not index

**Log line content.** Searching for `|= "checkout"` or `| json | error_reason = "timeout"` scans log line content. This is a full scan of all lines in the matching streams — O(n) in the number of lines.

### The implication for query design

| Query type | Speed | Cost |
|---|---|---|
| Label selection only: `{service="api"}` | Fast | Low |
| Label selection + content filter: `{service="api"} \|= "error"` | Moderate | Medium |
| Aggregation over large time range: `rate({service="api"}[24h])` | Slow | High |

Design label sets to narrow the stream selection as much as possible before content filtering. `{service="api", level="WARNING"} \|= "checkout"` is faster than `{service="api"} \|= "checkout"` because the first selector eliminates all INFO-level logs before content scanning.

### Loki vs Elasticsearch

| | Loki | Elasticsearch |
|---|---|---|
| Index | Labels only | Full text |
| Content search | O(n) scan | Inverted index lookup |
| Storage cost | Low (compressed chunks) | High (index overhead) |
| Write throughput | High | Moderate |
| Query flexibility | Moderate | High |

Loki is the right choice when you control the log format (structured JSON) and can query primarily by labels. Elasticsearch is better when you need full-text search across unstructured or legacy log formats.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start all 7 services |
| `curl http://localhost:3100/ready` | Check Loki health |
| `curl -s http://localhost:9080/targets \| grep -o 'service="[^"]*"'` | Check Promtail scrape targets |
| `./break.sh` | Set error rate to 50% |
| `./break.sh 0.02` | Restore normal error rate |
| `docker compose logs -f api \| head -5` | See raw JSON logs from the API |

| Grafana action | Where |
|---|---|
| Logs + Metrics dashboard | Dashboards → Lumio → Lumio API — Logs + Metrics |
| LogQL queries | Explore → Loki datasource |
| Log details | Click any log line → expand fields |

---

## What this doesn't do yet

| Issue | Impact | Fixed in |
|---|---|---|
| Logs and traces are not linked | Seeing a log line doesn't jump to the trace for that request | Phase 5 |
| Log alerting not configured | You cannot be paged based on log content | Phase 5 |
| No trace ID in logs | Cannot correlate a specific log line to its trace | Phase 5 |
| Logs retained only 72h | Long-term log analysis not possible in this config | Phase 9 |

---

## Production considerations

### 1. Always emit structured logs from day one
Retrofitting structured logging onto an existing codebase that emits free-text logs is expensive. Existing dashboards, alerts, and runbooks that rely on text patterns break when the format changes. Start with JSON logging before you have a log aggregation system — the cost is minimal and the migration later is free.

### 2. Do not promote high-cardinality fields to Loki labels
The most common Loki performance mistake is promoting `user_id`, `request_id`, `trace_id`, or `session_id` to labels. Each unique value becomes a separate stream. Loki becomes slow and its index explodes. Keep these as log content, queryable with `| json`. The rule: if a field has more than ~50 unique values, it is content, not a label.

### 3. Use `drop` stages in Promtail for volume control
High-traffic services can generate gigabytes of DEBUG logs per day. A Promtail `drop` stage removes lines before they reach Loki — zero storage cost, zero query cost. Drop DEBUG in production; keep them in dev where you need them.

### 4. Align log retention with your incident review cadence
If your post-incident review process requires logs from the last 30 days, configure retention at 35 days. If oncall resolution typically happens within 72 hours, 7-day retention is usually sufficient. Retention is a cost vs investigation window trade-off.

### 5. The `/var/run/docker.sock` mount has security implications
Mounting the Docker socket into Promtail gives Promtail (and anything that compromises it) full control over the Docker daemon. In production on Kubernetes, use the Loki log pipeline with the Promtail DaemonSet — it reads from `/var/log/pods` instead of the Docker socket, which is significantly less privileged.

---

## Outcome

The Lumio team can now see logs and metrics side by side in the same Grafana dashboard. When an alert fires, the oncall opens one screen — not metrics in Grafana and logs over SSH. The structured JSON format makes every log field queryable without regex guessing. Error reasons, event types, and processing durations are all first-class queryable fields in LogQL.

The missing piece: a log line tells you an event failed, but not which specific request's trace shows the full execution path. Connecting logs to traces is Phase 5.

---

[← Back to Phase 3 — OpenTelemetry](../phase-3-opentelemetry/README.md) | [Next: Phase 5 — Correlating Logs and Metrics →](../phase-5-correlation/README.md)
