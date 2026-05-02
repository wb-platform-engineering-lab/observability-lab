# Phase 3 — OpenTelemetry

> **Concepts introduced:** OpenTelemetry SDK, OTLP protocol, OTel Collector, distributed tracing, spans, span attributes, semantic conventions, traces in Grafana (Tempo), trace-to-metric correlation, dual-signal pipeline

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **OpenTelemetry (OTel)** | Vendor-neutral SDK and wire protocol for metrics, logs, and traces | One instrumentation layer works with any backend — Prometheus, Dynatrace, Datadog, Jaeger |
| **OTLP** | OpenTelemetry Protocol — HTTP or gRPC transport for telemetry data | The standard wire format between instrumented apps and observability backends |
| **OTel Collector** | A proxy and pipeline component that receives, processes, and exports telemetry | Decouples the app from its backends — the app sends to one place, the Collector fans out |
| **Distributed trace** | An end-to-end record of a request, composed of spans | Answers "where did 800ms go?" by showing exactly which function consumed time |
| **Span** | A named, timed unit of work within a trace | The atom of tracing — has a name, start time, duration, status, and key-value attributes |
| **Root span** | The first span in a trace — usually the HTTP request boundary | Created automatically by `FlaskInstrumentor`; child spans nest under it |
| **Child span** | A span nested under a parent span | Used for sub-operations: a DB query, an external call, a business logic step |
| **Span attributes** | Key-value metadata attached to a span | Make traces filterable and searchable: find all `event.type=checkout` traces where `error=true` |
| **Semantic conventions** | Standardised attribute names defined by the OTel specification | Makes telemetry consistent and tool-compatible: `service.name`, `http.method`, `db.system` |
| **Resource** | Process-level metadata attached to every signal from a process | `service.name`, `service.version`, `deployment.environment` — set once, carried everywhere |
| **Grafana Tempo** | An open-source distributed tracing backend | Stores traces; Grafana queries it via the Tempo datasource |
| **Trace-to-metric correlation** | Clicking a trace span in Grafana opens the related metric graph | Reduces context-switch during incidents — see the latency spike then jump to the trace |

---

## The problem

> *Lumio — 20 engineers. Six weeks after Phase 2.*
>
> The alerting was working. When the error rate climbed above 5%, the oncall engineer got paged within 5 minutes. Progress.
>
> But the alerts only said what was wrong — not why. The P95 latency for `/events/summary` had crept from 80ms to 340ms over three weeks. No single incident, just a slow drift. Every investigation hit the same wall: the dashboard showed the spike, but the code path was invisible.
>
> "I can see it's slow. I can't see where."
>
> One engineer spent a day instrumenting the service with the OpenTelemetry SDK. By the end of the day, every request had a trace. The latency drift was identified in 25 minutes: the `enrich-response` sub-operation in `/events/summary` had grown from 10ms to 180ms as the response payload size increased.
>
> The fix took 2 hours. The identification had previously taken weeks.

---

## Architecture

```
phase-3-opentelemetry/app/

  ┌──────────────────────────────────────┐
  │  lumio-api  :8000                    │
  │                                      │
  │  OTel SDK                            │
  │    ├── metrics ──┐                   │
  │    └── traces ───┤ OTLP gRPC :4317   │
  └──────────────────┼───────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────┐
  │  OTel Collector  :4317               │
  │                                      │
  │  receivers:  [otlp]                  │
  │  processors: [memory_limiter, batch] │
  │                                      │
  │  pipelines:                          │
  │    traces  → Tempo   :4317 (OTLP)   │
  │    metrics → Prometheus :8889 (scrape) │
  └──────────────────────────────────────┘
          │                   │
          ▼                   ▼
  ┌────────────┐     ┌─────────────────┐
  │  Tempo     │     │  Prometheus     │
  │  :3200     │     │  :9090          │
  └─────┬──────┘     └────────┬────────┘
        │                     │
        └──────────┬──────────┘
                   ▼
          ┌─────────────────┐
          │  Grafana  :3000  │
          │                 │
          │  Prometheus DS  │  ← metrics
          │  Tempo DS       │  ← traces
          └─────────────────┘
```

**Key insight:** The app sends to one endpoint — the Collector. The Collector decides where the data goes. Adding or removing a backend (Dynatrace, Loki, a second Prometheus) is a change to the Collector config, not to the application.

---

## Repository structure

```
phase-3-opentelemetry/
└── app/
    ├── docker-compose.yml
    ├── load.sh
    ├── api/
    │   ├── Dockerfile
    │   ├── app.py              ← OTel SDK setup + manual spans
    │   └── requirements.txt
    ├── otelcol/
    │   └── config.yml          ← receivers / processors / exporters / pipelines
    ├── tempo/
    │   └── tempo.yml
    ├── prometheus/
    │   └── prometheus.yml      ← scrapes otelcol:8889 (not the app)
    └── grafana/
        ├── provisioning/
        │   ├── datasources/
        │   │   ├── prometheus.yml
        │   │   └── tempo.yml   ← Tempo datasource with trace-to-metric config
        │   └── dashboards/
        │       └── lumio.yml
        └── dashboards/
            └── lumio-otel.json
```

---

## Challenge 1 — Start the 5-service stack

### Step 1: Start everything

```bash
cd phase-3-opentelemetry/app
docker compose up -d --build
```

This starts five containers: `api`, `otelcol`, `tempo`, `prometheus`, `grafana`. Wait ~15 seconds for all services to initialise.

```bash
docker compose ps
```

All five should show `running`.

### Step 2: Generate load

```bash
chmod +x load.sh && ./load.sh
```

### Step 3: Verify the pipeline is flowing

Check the Collector is receiving data:

```bash
docker compose logs otelcol | grep -E "TracesExporter|MetricsExporter" | tail -10
```

You should see log lines like `"msg":"Traces Exporter"` with non-zero span counts.

Check Prometheus is scraping the Collector's metrics endpoint:

```bash
curl -s http://localhost:8889/metrics | grep lumio | head -20
```

This is the Collector's Prometheus exporter — not the app. The app has no `/metrics` endpoint in Phase 3; it only speaks OTLP.

### Step 4: Open Grafana

Open **http://localhost:3000** (admin / lumio) → Dashboards → Lumio → **Lumio API — OpenTelemetry**.

The panels use the same PromQL as Phase 1. The data now flows through the Collector before reaching Prometheus.

> **Metric names:** The OTel Collector's Prometheus exporter converts instrument names the same way the SDK's direct Prometheus exporter does:
> - Dots → underscores: `lumio.http.requests` → `lumio_http_requests`
> - Counter `_total` suffix: → `lumio_http_requests_total`
> - Histogram unit: `lumio.http.request.duration` (unit=`s`) → `lumio_http_request_duration_seconds`
>
> This means the Phase 1 dashboard's PromQL queries work unchanged.

---

## Challenge 2 — Your first trace

### Step 1: Find traces in Grafana Explore

In Grafana, click **Explore** (compass icon) → select **Tempo** as the datasource.

Under **Query type**, select **Search**. Set:
- **Service name:** `lumio-api`

Click **Run query**. You will see a list of recent traces, each with a trace ID, root span name, duration, and timestamp.

### Step 2: Open a trace

Click any trace. The trace view opens:

```
POST /events  (root span — created by FlaskInstrumentor)  42ms
  └── process-event  (child span — created manually in app.py)  38ms
```

The root span is created automatically. The child span was added by the developer to show the processing step. The gap between the parent's start and the child's start is Flask overhead (routing, middleware).

### Step 3: Find a failed trace

In the search, add a filter:
- **Tags:** `error = true`

Run the query. These are the ~2% of requests that hit the simulated error path. Open one. The trace shows:
- `error = true` on the `process-event` span
- `error.reason = validation_error` (or `schema_mismatch` / `timeout`)

Compare to what you had before Phase 3: the metrics showed an error rate of ~2%, the logs showed individual error lines. What was missing: the full request context for each failure. The trace ties them together — one trace ID links the metric data point, the log line, and the code path.

### Step 4: Open a slow trace

Look at traces for `GET /events/summary`. The duration is between 50–200ms. Open a long one (> 150ms).

```
GET /events/summary  (root span)  180ms
  └── aggregate-events  170ms
        ├── fetch-counts  140ms
        └── enrich-response  25ms
```

The nested spans show where time was spent. `fetch-counts` consumed 140ms of the 170ms. Without traces, you would only know the request was slow — not that `fetch-counts` was the bottleneck.

---

## Challenge 3 — Understand the OTel Collector config

Open `otelcol/config.yml`. The structure is always:

```
receivers  → where data comes in
processors → what to do with it in flight
exporters  → where data goes out
service    → wire receivers + processors + exporters into named pipelines
```

### The pipeline definition

```yaml
service:
  pipelines:
    traces:
      receivers:  [otlp]
      processors: [memory_limiter, batch]
      exporters:  [otlp/tempo, debug]
    metrics:
      receivers:  [otlp]
      processors: [memory_limiter, batch]
      exporters:  [prometheus]
```

Read this as: "the `traces` pipeline takes data from the `otlp` receiver, runs it through `memory_limiter` then `batch`, and sends it to `otlp/tempo` and `debug`."

Two pipelines share the same receiver — the Collector fans out automatically. Adding a third pipeline (e.g., `logs`) would add a third entry here.

### The memory_limiter processor

```yaml
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 256
```

The Collector will drop data rather than OOM if it exceeds 256MB. This must be the **first** processor in every pipeline. Without it, a sudden traffic spike can kill the Collector, taking down observability at the exact moment you need it most.

### The batch processor

```yaml
processors:
  batch:
    timeout: 5s
    send_batch_size: 512
```

Groups spans and metric datapoints into batches before export. Sending 512 spans as one gRPC call is far more efficient than 512 individual calls. The `timeout: 5s` means even small batches are flushed within 5 seconds.

### The prometheus exporter

```yaml
exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"
    metric_expiration: 3m
```

Unlike all other exporters (which push), the Prometheus exporter starts an HTTP server that Prometheus scrapes. `metric_expiration: 3m` removes a series from the scrape endpoint if no data has arrived for 3 minutes — prevents stale metrics lingering after the app restarts.

---

## Challenge 4 — Read the OTel SDK setup in app.py

Open `api/app.py`. The setup follows a consistent pattern for both metrics and traces.

### Resource — process-level metadata

```python
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME:           "lumio-api",
    ResourceAttributes.SERVICE_VERSION:        "1.0.0",
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: ENVIRONMENT,
})
```

The resource is not a metric label or a span attribute — it is metadata about the **source** of the telemetry. Every metric, trace, and log emitted by this process carries these attributes. In Tempo, `service.name` appears as the service filter. In Prometheus, `service_name` appears as a label on every scraped metric.

Using `ResourceAttributes.SERVICE_NAME` (a constant from `opentelemetry-semantic-conventions`) instead of the raw string `"service.name"` is correct — it prevents typos and makes the code self-documenting.

### MeterProvider and TracerProvider

```python
meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True),
            export_interval_millis=15_000,
        )
    ],
)
metrics.set_meter_provider(meter_provider)
```

```python
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
    )
)
trace.set_tracer_provider(tracer_provider)
```

Both providers are configured with the same resource. Both send to the same `OTEL_ENDPOINT` (the Collector). The providers are registered globally — `metrics.set_meter_provider(...)` and `trace.set_tracer_provider(...)` — so any code that calls `metrics.get_meter(...)` or `trace.get_tracer(...)` gets an instrument that writes through these providers.

### FlaskInstrumentor

```python
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
```

This one line adds middleware that creates a root span for every HTTP request. The span name is `METHOD route` (e.g., `POST /events`). It captures HTTP status code, route, and method automatically. Without it, you would need to write `with tracer.start_as_current_span(...)` wrapping every route handler manually.

---

## Challenge 5 — Add a manual span

The `/events/summary` route already has two child spans (`fetch-counts` and `enrich-response`). Add a third that measures the JSON serialisation step.

### Step 1: Find the route

In `app.py`, find the `events_summary` function.

### Step 2: Add a span

After the `enrich-response` span, add:

```python
with tracer.start_as_current_span("serialise-response") as span:
    result = {"summary": counts}
    span.set_attribute("lumio.payload_keys", len(result["summary"]))
    return jsonify(result)
```

Remove the existing `return` statement from inside `enrich-response`.

### Step 3: Rebuild and verify

```bash
docker compose up -d --build api
```

Send a few requests to `/events/summary`:

```bash
curl -s http://localhost:8000/events/summary | python3 -m json.tool
```

Open a trace in Grafana. The `aggregate-events` span now has three children:

```
aggregate-events
  ├── fetch-counts
  ├── enrich-response
  └── serialise-response   ← new
```

The `serialise-response` span will be very short (< 1ms) — JSON serialisation of a small dict is fast. This is useful to know: if payload sizes grow significantly, this span will tell you.

> **When to add a span:** Add a span when the timing of a sub-operation is useful to know during an incident. Database queries, external HTTP calls, slow loops, and business logic steps are good candidates. Do not span every function — that creates noise and overhead.

---

## Challenge 6 — Semantic conventions

Open `app.py` and look at the span attributes used on the `process-event` span:

```python
span.set_attribute("event.type", event_type)
span.set_attribute("lumio.pipeline", "ingest")
span.set_attribute("error.reason", reason)
```

These are **custom attributes** — they follow a sensible naming convention but are not standardised. Compare to the resource attributes:

```python
ResourceAttributes.SERVICE_NAME           # "service.name"
ResourceAttributes.SERVICE_VERSION        # "service.version"
ResourceAttributes.DEPLOYMENT_ENVIRONMENT # "deployment.environment"
```

These are **semantic conventions** — attribute names defined by the OTel specification and used consistently across all languages and frameworks. Any tool that understands OTel semantic conventions can interpret them without custom configuration.

### Where semantic conventions matter

| Signal | Convention | Value |
|---|---|---|
| HTTP request | `http.method` | `GET`, `POST` |
| HTTP request | `http.route` | `/events` |
| HTTP response | `http.status_code` | `200`, `500` |
| Database call | `db.system` | `postgresql`, `redis` |
| Database call | `db.statement` | `SELECT * FROM events` |
| External call | `net.peer.name` | `api.stripe.com` |
| Message queue | `messaging.system` | `kafka`, `rabbitmq` |

`FlaskInstrumentor` sets `http.method`, `http.route`, and `http.status_code` automatically using these conventions. When you add a database instrumentation library later, it will set `db.system` and `db.statement` using the same conventions. Grafana, Dynatrace, Datadog, and every other tool knows how to interpret these without configuration.

### The rule

Use semantic convention constants (from `opentelemetry-semantic-conventions`) for attributes that have a defined convention. Use your own namespaced attributes (like `lumio.pipeline`) for domain-specific data that has no standard.

---

## Challenge 7 — Correlate trace to metric

Grafana's Tempo datasource is configured with `tracesToMetrics`. This creates a link from a trace span directly to a Prometheus query scoped to the same time window.

### Step 1: Open a trace

In Grafana Explore → Tempo, find a trace for `POST /events`.

### Step 2: Use the trace-to-metrics link

In the trace view, click the **Metrics** button (or the link icon) on the root span.

Grafana opens a new Explore panel showing the `sum(rate(lumio_http_requests_total[5m])) by (endpoint)` query, scoped to a 4-minute window centred on the trace's timestamp.

This lets you move in both directions:
- **Metric spike → trace:** You see a P95 latency spike in the dashboard, click "View traces", jump directly to traces from that time window
- **Trace → metric:** You are investigating a specific request, click the metric link, see whether the latency was an isolated event or part of a broader pattern

### Step 3: Why this matters

Before Phase 3, answering "was this slow request part of a pattern?" required:
1. Note the timestamp from the log
2. Switch to Grafana
3. Adjust the time range manually
4. Find the right panel

Now it is one click. The time context is preserved. This is the practical value of having metrics and traces in the same Grafana instance with correlation configured.

---

## Challenge 8 — TraceQL: query traces like a database

Tempo supports **TraceQL** — a query language for traces similar to LogQL for logs.

In Grafana Explore → Tempo, switch to **TraceQL** query type.

### Query 1: All failed traces

```
{ span.error = true }
```

Returns all spans where `error = true` was set.

### Query 2: Slow event processing

```
{ span.lumio.pipeline = "ingest" } | duration > 40ms
```

Returns traces where the `ingest` pipeline span took more than 40ms. This is the population of requests in the slow tail — not aggregated, individual.

### Query 3: Errors by reason

```
{ span.error.reason = "timeout" }
```

Find only the timeout failures. Compare with:

```
{ span.error.reason = "validation_error" }
```

With PromQL you can see that errors are happening. With TraceQL you can ask which specific code path failed, for which input, on which request.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start all 5 services |
| `docker compose ps` | Verify all containers are running |
| `docker compose logs -f otelcol` | Stream Collector logs (shows span/metric throughput) |
| `docker compose logs -f tempo` | Stream Tempo logs |
| `curl http://localhost:8889/metrics \| grep lumio` | Inspect metrics at the Collector's Prometheus exporter |
| `docker compose restart api` | Rebuild after code changes |

| Grafana action | Where |
|---|---|
| Browse traces | Explore → Tempo → Search |
| TraceQL queries | Explore → Tempo → TraceQL |
| Metric dashboard | Dashboards → Lumio → Lumio API — OpenTelemetry |
| Trace-to-metric link | Open any trace → click Metrics button on a span |

---

## What this doesn't do yet

| Issue | Impact | Fixed in |
|---|---|---|
| Traces are not correlated to log lines | Seeing a trace doesn't jump to the log that accompanied it | Phase 5 |
| No structured logs in Loki | Logs are still scattered, not centralised | Phase 4 |
| No multi-service traces | All spans are within one process — no propagation across service boundaries | Capstone |
| Tempo retention is 1 hour | Traces are discarded quickly in this lab config | Phase 10 |

---

## Production considerations

### 1. Run the Collector as a sidecar or DaemonSet, not a shared service
In production on Kubernetes, deploy the Collector as a sidecar (one per pod) or DaemonSet (one per node). A single shared Collector becomes a single point of failure — if it goes down, the entire observability pipeline for all services stops. Each app sending to a local Collector also reduces latency and improves isolation.

### 2. Use gRPC for OTLP, not HTTP
The gRPC exporter (`opentelemetry-exporter-otlp-proto-grpc`) uses persistent connections and binary encoding — significantly more efficient than HTTP/protobuf for high-volume services. Use HTTP only when gRPC is blocked (e.g., some legacy proxies or load balancers).

### 3. Pin OTel package versions as a group
The OTel Python packages are versioned across two tracks. Core packages (`opentelemetry-api`, `opentelemetry-sdk`) follow `1.x.x`. Instrumentation and exporter packages follow `0.x` (beta). They must be upgraded together. A mismatched `opentelemetry-api` and `opentelemetry-instrumentation-flask` will cause import errors or silent export failures at startup. Pin all OTel packages in a single requirements file and upgrade them as a unit.

### 4. Set sampling in the Collector, not the SDK
For high-traffic services, storing every trace is expensive. Configure tail-based sampling in the Collector using the `tail_sampling` processor:
```yaml
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: keep-errors
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: keep-slow
        type: latency
        latency: { threshold_ms: 500 }
      - name: probabilistic-rest
        type: probabilistic
        probabilistic: { sampling_percentage: 10 }
```
This keeps 100% of errors and slow traces, and 10% of healthy fast traces. Sampling in the Collector means all traces are emitted by the app (preserving full trace context for sampling decisions) but only the interesting ones are stored.

### 5. The Collector is not optional at scale
In Phase 0–2, the app exported directly to Prometheus. For traces, a direct exporter to Tempo or Jaeger is possible. But at scale:
- The Collector adds buffering (protects backends from bursts)
- The Collector adds tail sampling (reduces storage cost)
- The Collector fans out (one app config → multiple backends)
- The Collector can be upgraded without redeploying the app

At more than a handful of services, running without a Collector creates operational debt that compounds quickly.

---

## Outcome

The Lumio team can now follow a slow request from the dashboard metric spike into the individual trace, see exactly which sub-operation was slow, and jump back to the metric view to confirm whether it was an isolated event or a pattern. The instrumentation is vendor-neutral — the app sends OTLP to the Collector and does not know or care whether Tempo, Dynatrace, or Jaeger receives the traces.

The Collector config is the single place where backend destinations are managed. Adding a new backend in Phase 11 will require a config change to `otelcol/config.yml` and nothing else.

---

[← Back to Phase 2 — Alerting](../phase-2-alerting/README.md) | [Next: Phase 4 — Log Aggregation with Loki →](../phase-4-loki/README.md)
