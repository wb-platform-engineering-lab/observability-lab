# Phase 5 — Correlating Logs, Metrics, and Traces

> **Concepts introduced:** Trace context propagation into logs, trace_id in structured logs, Loki derived fields, Tempo tracesToLogs, Tempo tracesToMetrics, the three correlation directions, incident investigation workflow

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Trace context in logs** | Injecting `trace_id` and `span_id` into every log line emitted inside a span | Ties a log line to the exact request that produced it — across services |
| **`get_current_span()`** | OTel SDK function that returns the active span in the current thread | Called inside the JSON log formatter — no manual plumbing required |
| **Loki derived fields** | A Grafana config that extracts a value from a log line and turns it into a link | Clicking `trace_id` in a log line opens that trace in Tempo |
| **Tempo tracesToLogs** | A Grafana config that generates a LogQL query from a trace's span attributes | Clicking "Logs" on a span opens Loki filtered to that trace |
| **Tempo tracesToMetrics** | A Grafana config that opens Prometheus queries scoped to a span's time window | Clicking "Metrics" on a span shows the service metrics at the time of that request |
| **The three correlation directions** | Log→Trace, Trace→Log, Trace→Metric | Together they let you start from any signal and reach any other |
| **Time-range correlation** | Grafana's shared time range across Explore panels | The coarsest form of correlation — look at the same clock window in all three systems |

---

## The problem

> *Lumio — 30 engineers. Two weeks after Phase 4.*
>
> Logs were in Loki. Traces were in Tempo. Metrics were in Prometheus. Three panels on one screen, and still three separate investigations.
>
> An alert fired at 2:47am. Error rate at 11%. The oncall opened the dashboard. Top panel: error rate spike confirmed, started at 2:43. Middle panel: WARNING log volume spiked at the same time. Bottom panel: individual error log lines — `error_reason: timeout`, `event_type: checkout`.
>
> "OK, checkout events are timing out. But *why*? Is this one slow span? A dependency? A specific code path?"
>
> They had to manually copy a timestamp, open Tempo, set the time range, search for checkout traces from 2:43–2:47, find a slow one, open it. Ten minutes of context-switching between tabs.
>
> The fix was one line of code: inject `trace_id` into the log formatter. From then on, every error log line had a clickable link directly to its trace. The next incident: alert at 3:12am, log→trace link clicked at 3:13am, root cause identified at 3:16am. Four minutes total.

---

## Architecture

```
phase-5-correlation/app/

  ┌─────────────────────────────────────────────────────────────┐
  │  lumio-api                                                  │
  │                                                             │
  │  OTel SDK — metrics + traces → otelcol:4317 (OTLP)         │
  │  JSONFormatter — logs to stdout with trace_id injected      │
  └──────────────┬───────────────────────────┬──────────────────┘
                 │ OTLP                       │ stdout (JSON)
                 ▼                            ▼
  ┌──────────────────────┐     ┌───────────────────────┐
  │  OTel Collector      │     │  Promtail             │
  │  traces → Tempo      │     │  (Docker SD)          │
  │  metrics → :8889     │     │  extracts trace_id    │
  └───────┬──────────────┘     └──────────┬────────────┘
          │                               │ push
          ▼                               ▼
  ┌───────────┐  ┌────────────┐   ┌──────────────┐
  │  Tempo    │  │ Prometheus │   │     Loki     │
  │  :3200    │  │  :9090     │   │     :3100    │
  └─────┬─────┘  └──────┬─────┘   └──────┬───────┘
        │                │                │
        └────────────────┴────────────────┘
                         │
                  ┌──────▼──────────────────────┐
                  │  Grafana  :3000              │
                  │                             │
                  │  Tempo DS ←tracesToLogs──► Loki DS
                  │           ←tracesToMetrics─► Prometheus DS
                  │  Loki DS  ←derivedFields──► Tempo DS
                  └─────────────────────────────┘
```

The correlation links are bidirectional — every signal can reach every other signal in one click.

---

## Repository structure

```
phase-5-correlation/
└── app/
    ├── docker-compose.yml         ← 9 services
    ├── load.sh
    ├── break.sh                   ← triggers errors; prints the correlation workflow
    ├── api/
    │   └── app.py                 ← Phase 3 OTel + Phase 4 logging + trace_id injection
    ├── otelcol/config.yml
    ├── tempo/tempo.yml
    ├── loki/loki.yml
    ├── promtail/promtail.yml      ← extracts trace_id from JSON (not as label)
    ├── prometheus/
    ├── alertmanager/
    ├── webhook/
    └── grafana/
        ├── provisioning/
        │   └── datasources/
        │       ├── tempo.yml      ← tracesToLogs + tracesToMetrics
        │       └── loki.yml       ← derivedFields → Tempo link
        └── dashboards/
            └── lumio-full.json    ← metrics + log volume + live logs in one view
```

---

## Challenge 1 — Start the full stack

```bash
cd phase-5-correlation/app
docker compose up -d --build
```

Nine containers start. Wait ~20 seconds for all services to initialise.

```bash
docker compose ps
```

Verify all services are running. Then:

```bash
chmod +x load.sh && ./load.sh
```

Open **http://localhost:3000** (admin / lumio) → **Dashboards → Lumio → Lumio API — Full Stack**.

You will see:
- Row 1: Prometheus metrics — RPS, error rate, P95 latency
- Row 2: Latency percentiles (Prometheus) + log volume by level (Loki)
- Row 3: Live application log lines

The WARNING log lines will have a `trace_id` field. We will use this in Challenge 3.

---

## Challenge 2 — Understand trace_id injection

### The old way (Phase 4)

In Phase 4, log lines looked like this:

```json
{"timestamp":"...","level":"WARNING","message":"event processing failed",
 "event_type":"checkout","error_reason":"timeout","duration_ms":47.2}
```

Useful — structured, queryable by field. But there is no way to know which specific HTTP request this log line belongs to.

### The new way (Phase 5)

```json
{"timestamp":"...","level":"WARNING","message":"event processing failed",
 "event_type":"checkout","error_reason":"timeout","duration_ms":47.2,
 "trace_id":"4bf92f3577b34da6a3ce929d0e0e4736",
 "span_id":"00f067aa0ba902b7"}
```

Two new fields: `trace_id` and `span_id`. These come from the active OTel span at the moment the log was emitted. Every log line produced during the handling of that HTTP request carries the same `trace_id`.

### How it works in code

Open `api/app.py` and find the `JSONFormatter.format` method:

```python
span = otel_trace.get_current_span()
ctx  = span.get_span_context()
if ctx.is_valid:
    log_obj["trace_id"] = format(ctx.trace_id, "032x")
    log_obj["span_id"]  = format(ctx.span_id,  "016x")
```

`otel_trace.get_current_span()` returns the span that is currently active on this thread. Because `FlaskInstrumentor` creates a root span for every HTTP request, and our manual spans (`process-event`, `aggregate-events`) nest under it, any `logger.info()` or `logger.warning()` call made while handling a request is always inside an active span.

`ctx.is_valid` returns `False` for background log lines (startup, shutdown) where no request is being processed. Those lines simply have no `trace_id` — no error, no injection.

The key property: this works automatically for all log calls made within a span context. No extra arguments, no thread-local state to manage manually. `get_current_span()` uses Python's `contextvars` under the hood — safe for async and threaded code.

---

## Challenge 3 — Log → Trace: follow a log line to its trace

### Step 1: Trigger errors to get WARNING logs with trace IDs

```bash
./break.sh
```

### Step 2: Find an error log

Open **Grafana Explore → Loki** and run:

```
{service="api", level="WARNING"}
```

Click on any log line to expand its details. You will see the `trace_id` and `error_reason` fields.

### Step 3: Click the trace link

In the expanded log details, you will see a field labelled **TraceID** with an **Open trace in Tempo** link. Click it.

Tempo opens and shows the full trace for that exact HTTP request:

```
POST /events  (root span — FlaskInstrumentor)  48ms
  └── process-event  (manual span)  44ms
        attributes: event.type=checkout, error=true, error.reason=timeout
```

This is the complete execution path for the specific request that produced the log line you clicked. No manual time-range adjustment, no trace ID copy-paste — one click.

### How the link is configured

Open `grafana/provisioning/datasources/loki.yml`:

```yaml
derivedFields:
  - name: TraceID
    matcherRegex: '"trace_id":"([a-f0-9]{32})"'
    url: '${__value.raw}'
    datasourceUid: tempo
    urlDisplayLabel: "Open trace in Tempo"
```

- `matcherRegex` — scans each log line. The capture group `([a-f0-9]{32})` extracts the 32-character hex trace ID.
- `url` — `${__value.raw}` is replaced with the captured value. Grafana recognises this as a Tempo trace ID and opens the Tempo datasource for it.
- `datasourceUid: tempo` — the link opens in the Tempo datasource, not as an external URL.

Grafana evaluates this regex against every log line it renders. Lines without a `trace_id` field simply have no link. Lines with one get the clickable button.

---

## Challenge 4 — Trace → Log: from a span to its log lines

### Step 1: Find a trace in Tempo

Open **Grafana Explore → Tempo → Search**. Set service `lumio-api`. Select any trace for `POST /events`.

### Step 2: Use the Logs button

In the trace view, click the **Logs** button (or the Loki icon) on the root span or the `process-event` child span.

Grafana runs this LogQL query against Loki, scoped to a time window around the span:

```
{service="api"} | json | trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
```

The result is every log line emitted during that specific request. For a successful request you will see one INFO line. For a failed request you will see one WARNING line with the error reason.

### How the link is configured

Open `grafana/provisioning/datasources/tempo.yml`:

```yaml
tracesToLogs:
  datasourceUid: loki
  filterByTraceID: false
  customQuery: true
  query: '{service="api"} | json | trace_id = "${__trace.traceId}"'
```

`${__trace.traceId}` is a Grafana template variable substituted with the actual trace ID at query time. The `{service="api"}` label selector narrows the search to the API's log stream before applying the JSON filter — faster than a full-scan query.

---

## Challenge 5 — Trace → Metric: from a span to the service state at that moment

### Step 1: Open a trace for a slow summary request

In Grafana Explore → Tempo, search for traces of `GET /events/summary`. Find one with duration > 150ms.

### Step 2: Click the Metrics button

In the trace view, click **Metrics** on the root span. Grafana opens three Prometheus queries scoped to a 4-minute window centred on the span's timestamp:

- **Request rate:** `sum(rate(lumio_http_requests_total[5m])) by (endpoint)`
- **P95 latency:** `histogram_quantile(0.95, ...)`
- **Error rate:** `100 * ...`

This answers the question: "was this slow request an isolated event, or was the whole service degraded at that moment?" If the P95 chart shows a spike at that timestamp, the degradation was systemic. If it's flat, the request was an outlier.

### How the link is configured

```yaml
tracesToMetrics:
  datasourceUid: prometheus
  spanStartTimeShift: "-2m"
  spanEndTimeShift: "2m"
  queries:
    - name: Request rate
      query: sum(rate(lumio_http_requests_total[5m])) by (endpoint)
    - name: P95 latency
      query: histogram_quantile(0.95, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
```

The `spanStartTimeShift` and `spanEndTimeShift` set the time range padding around the span. Without padding, a span that lasted 50ms would result in a 50ms time window in Prometheus — too narrow for `rate()` calculations. The 2-minute padding gives the rate functions enough data.

---

## Challenge 6 — Metric → Trace: from a spike to the traces that caused it

The reverse direction: you see a latency spike in Grafana, and want to find the specific requests that were slow.

### Step 1: Identify the spike window

In the **Lumio API — Full Stack** dashboard, find a moment where P95 latency is elevated. Note the time range (e.g. 14:23–14:25).

### Step 2: Search traces for that window in TraceQL

In **Grafana Explore → Tempo**, switch to **TraceQL** and run:

```
{ span.lumio.pipeline = "summary" } | duration > 150ms
```

This returns all `aggregate-events` spans (from the summary endpoint) that took more than 150ms — the requests in the slow tail.

### Step 3: Correlate with logs

Pick the slowest trace. Open it. The span breakdown shows which child span (`fetch-counts` or `enrich-response`) consumed the time. Click **Logs** to see the log line for that request.

You now have the full picture:
- **Metric:** P95 was elevated from 14:23–14:25
- **Trace:** `aggregate-events` span, `fetch-counts` took 148ms of 160ms total
- **Log:** `{"message":"summary generated","total":47823,"duration_ms":160}`

Three signals, three clicks, one incident understood.

---

## Challenge 7 — Time-range correlation: the coarsest but always-available form

Not every log line has a trace_id. Not every metric has an exemplar. But every signal has a timestamp, and Grafana's time range is shared across all Explore panels.

### The workflow

1. Alert fires — you open **Alertmanager** at http://localhost:9093 and see `LumioHighErrorRate` fired at 14:23.
2. Set Grafana's time range to 14:20–14:30.
3. Open two Explore tabs side by side: one for Loki, one for Tempo.
4. Both tabs show data from the same 10-minute window.

This is the baseline. When derivedFields and tracesToLogs are configured, you can do better. But time-range correlation requires zero configuration — it works immediately.

**Open two panels side by side in Grafana Explore:**

```
Left panel  (Prometheus): sum(rate(lumio_http_requests_total{status_code=~"5.."}[5m]))
Right panel (Loki):       {service="api", level="WARNING"} | json
```

Both panels share the time range selector at the top. Drag to select a spike in the left panel — the right panel updates to show logs from that exact window.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start all 9 services |
| `./load.sh` | Generate traffic |
| `./break.sh` | Set error rate 50%, prints correlation workflow steps |
| `./break.sh 0.02` | Restore |
| `docker compose logs -f api \| python3 -m json.tool` | Pretty-print structured JSON logs |

| Grafana action | Where |
|---|---|
| Full-stack dashboard | Dashboards → Lumio → Lumio API — Full Stack |
| Log → Trace | Explore → Loki → expand log line → click TraceID link |
| Trace → Log | Explore → Tempo → open trace → click Logs button on span |
| Trace → Metric | Explore → Tempo → open trace → click Metrics button on span |
| Metric → Trace | Explore → Tempo → TraceQL: `{ span.lumio.pipeline = "ingest" } \| duration > 40ms` |

---

## What this doesn't do yet

| Issue | Impact | Fixed in |
|---|---|---|
| Traces only within one service | No distributed trace across multiple services | Phase 10 |
| Prometheus scraping via Collector | Prometheus has no direct `/metrics` from the app — cardinality analysis harder | Phase 8 |
| No recording rules | Expensive histogram quantile queries run on every dashboard load | Phase 6 |
| No infrastructure metrics | Host CPU/disk/memory not visible | Phase 7 |

---

## Production considerations

### 1. `get_current_span()` is always safe to call
The function never raises. When called outside any span context (startup code, background threads, cron jobs), it returns a no-op span whose `get_span_context().is_valid` is `False`. Guard with `if ctx.is_valid` before injecting — log lines without a span context simply have no trace_id. This is correct behaviour.

### 2. trace_id must be log content, not a Loki label
Every HTTP request has a unique trace_id. Promoting it to a Loki label would create one stream per request — millions of streams, catastrophic memory usage. Keep it as log content and use Grafana's derived fields config to extract it for linking. Loki's `| json | trace_id = "..."` filter is a content scan, not a label lookup — slower, but the only option for high-cardinality identifiers.

### 3. Derived fields only work on log lines Grafana has already fetched
The regex in `derivedFields` is applied in the Grafana browser layer, not in Loki. Grafana fetches log lines matching your LogQL query, then scans each line for the regex. If you filter to `{service="api", level="WARNING"}`, derived fields only appear on WARNING lines — not on INFO lines that weren't fetched. This is expected behaviour.

### 4. The `customQuery` in tracesToLogs must match your Loki label structure
`{service="api"}` assumes the container is labelled `service=api` in Loki. If your Promtail config uses different label names (e.g. `app`, `container`, `deployment`), update the custom query to match. Mismatched labels return empty results with no error — easy to misdiagnose as "no logs exist for this trace."

### 5. All three datasources in Grafana should share the same clock source
If your Prometheus, Loki, and Tempo instances are in different timezones or have clock drift, correlation by time range breaks. Use NTP on all hosts. In Kubernetes, pod clocks inherit from the node — ensure node time synchronisation is enforced.

---

## Outcome

The Lumio team's incident investigation workflow is now:

```
Alert fires
    ↓
Open dashboard → confirm error rate spike (Prometheus)
    ↓
Scroll to log panel → find error log → expand → click trace_id link (Loki → Tempo)
    ↓
Read the trace → click Logs button on the failing span (Tempo → Loki)
    ↓
All logs for that specific request, in context
```

The average time from alert to root cause identified dropped from 25 minutes (separate tools, manual copy-paste) to under 5 minutes (one tab, three clicks).

The three pillars are no longer separate systems that happen to run at the same company. They are correlated signals that together answer a question no single signal can answer alone: **what happened, in what order, in which code path, for which request**.

---

[← Back to Phase 4 — Log Aggregation with Loki](../phase-4-loki/README.md) | [Next: Phase 6 — Recording Rules and Query Optimisation →](../phase-6-recording-rules/README.md)
