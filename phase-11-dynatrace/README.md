# Phase 11 — Enterprise APM with Dynatrace

> **Concepts introduced:** OpenTelemetry SDK, OTLP protocol, dual telemetry pipeline, distributed tracing, span attributes, Dynatrace OneAgent, Smartscape topology, Davis AI, SLO management, build-vs-buy analysis

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **OpenTelemetry (OTel)** | Vendor-neutral SDK + wire protocol for metrics, logs, and traces | One instrumentation layer that works with any backend — Prometheus, Dynatrace, Datadog, Jaeger |
| **OTLP** | OpenTelemetry Protocol — gRPC/HTTP transport for telemetry | The lingua franca between instrumented apps and observability backends |
| **Dual pipeline** | Single OTel SDK emitting to two backends simultaneously | Migrate gradually: keep Prometheus, add Dynatrace for evaluation, compare before switching |
| **Distributed trace** | End-to-end record of a request across services, composed of spans | Answers "where did 800ms go?" by showing exactly which function/service consumed time |
| **Span** | A named, timed unit of work within a trace | The atom of distributed tracing — has start time, duration, status, and attributes |
| **Span attributes** | Key-value pairs attached to a span | Make traces searchable: find all traces where `event.type = checkout` and `error = true` |
| **Flask auto-instrumentation** | OTel middleware that creates a span for every HTTP request automatically | Zero code change — install the package, call `FlaskInstrumentor().instrument_app(app)` |
| **Dynatrace OneAgent** | A single agent that auto-instruments the host, containers, and processes | No code changes required for basic APM — but manual OTel gives richer semantic attributes |
| **Smartscape** | Dynatrace's real-time topology map | Shows service dependencies automatically — which services call which, and what changed before the incident |
| **Davis AI** | Dynatrace's AI engine for anomaly detection and root cause analysis | Correlates events across all signals (metrics, traces, logs, topology) to identify root cause without manual investigation |
| **Dynatrace SLO** | Service-level objective defined and tracked in Dynatrace | Dynatrace can automatically alert when an SLO is at risk of being missed, including burn rate calculation |
| **Build vs buy** | Choosing between self-managed OSS stack and a commercial platform | Not a binary choice — many teams run both: OTel for instrumentation portability, a commercial backend for analysis |

---

## The problem

> *Lumio — 100 engineers. Twelve months after the Phase 10 capstone.*
>
> The full observability platform was running. Prometheus, Grafana, Loki, Alertmanager, recording rules — the whole stack. It worked. When something broke, the team knew in minutes, not hours.
>
> But the stack had become infrastructure in itself. Two engineers owned it full-time. Every quarter brought a Prometheus upgrade, a Loki schema migration, an Alertmanager config refactor. When the team hit 100 engineers, the cost of running the stack was measurable: 2 FTE × €120k = €240k/year, plus compute and storage.
>
> The CTO asked the question that always comes eventually: "At what size does it make more sense to buy this than to build it?"
>
> A Dynatrace trial was spun up on a Friday afternoon. The team gave it two weeks.

---

## Architecture

```
phase-11-dynatrace/app/

  ┌──────────────────────────────────────────────────────────────────────┐
  │  lumio-api (OpenTelemetry SDK)                                       │
  │                                                                      │
  │  OTel meter + tracer                                                 │
  │        │                                                             │
  │        ├── PrometheusMetricReader ──► GET /metrics (scrape)          │
  │        │                                    │                        │
  │        │                             ┌──────▼──────┐                │
  │        │                             │ Prometheus  │◄── PromQL ──── Grafana │
  │        │                             │   :9090     │                │
  │        │                             └─────────────┘                │
  │        │                                                             │
  │        └── OTLPMetricExporter ──► Dynatrace SaaS (metrics)          │
  │        └── OTLPSpanExporter   ──► Dynatrace SaaS (traces)           │
  │                                                                      │
  │  FlaskInstrumentor (auto-spans every request)                        │
  └──────────────────────────────────────────────────────────────────────┘

  Prometheus stack: always active
  Dynatrace pipeline: active only when DT_ENDPOINT + DT_API_TOKEN are set
```

The key architectural insight: **one instrumentation layer, two consumers**. The OTel SDK does not know or care who is receiving the telemetry. Swapping or adding backends is a config change, not a code change.

---

## Repository structure

```
phase-11-dynatrace/
└── app/
    ├── docker-compose.yml         ← DT_ENDPOINT / DT_API_TOKEN via .env
    ├── load.sh
    ├── api/
    │   ├── Dockerfile
    │   ├── app.py                 ← OTel SDK: dual pipeline + Flask auto-instrumentation
    │   └── requirements.txt
    ├── prometheus/
    │   └── prometheus.yml
    └── grafana/
        ├── provisioning/
        │   ├── datasources/prometheus.yml
        │   └── dashboards/lumio.yml
        └── dashboards/
            └── lumio-otel.json   ← same PromQL queries, OTel-named metrics
```

---

## Challenge 1 — Run the stack without Dynatrace

Before connecting Dynatrace, verify the dual pipeline works in its Prometheus-only mode.

### Step 1: Start the stack

```bash
cd phase-11-dynatrace/app
docker compose up -d --build
```

### Step 2: Generate load

```bash
chmod +x load.sh && ./load.sh
```

### Step 3: Verify metrics at the scrape endpoint

```bash
curl -s http://localhost:8000/metrics | grep lumio
```

You will see metric names like:

```
lumio_http_requests_total{endpoint="ingest_event",method="POST",status_code="202"} 47.0
lumio_http_request_duration_seconds_bucket{le="0.1",...} 43.0
lumio_active_requests{endpoint="ingest_event"} 0.0
lumio_events_processed_total{event_type="page_view"} 12.0
```

> **OTel naming to Prometheus naming:** The OTel Prometheus exporter converts instrument names automatically:
> - Dots → underscores: `lumio.http.requests` → `lumio_http_requests`
> - Counter → `_total` suffix appended: → `lumio_http_requests_total`
> - Histogram unit appended: `lumio.http.request.duration` (unit=`s`) → `lumio_http_request_duration_seconds`
>
> This means the Grafana dashboard from Phase 1 works against Phase 11's app without any changes to the PromQL queries.

### Step 4: Check the Grafana dashboard

Open **http://localhost:3000** (admin / lumio) → Dashboards → Lumio → **Lumio API — OpenTelemetry**.

The panels use the same PromQL as Phase 1. The data comes from OTel metrics scraped by Prometheus.

---

## Challenge 2 — Connect to Dynatrace

### Step 1: Create a free Dynatrace trial

Go to **https://www.dynatrace.com/trial** and sign up. You will receive:
- An **Environment ID** — the subdomain of your SaaS environment (e.g. `abc12345`)
- Your environment URL: `https://abc12345.live.dynatrace.com`

No credit card is required. The trial runs for 15 days with full platform access.

### Step 2: Generate an API token

In your Dynatrace environment: **Settings → Access tokens → Generate new token**.

Give it the following scopes:
- `metrics.ingest` — push OTLP metrics
- `logs.ingest` — push OTLP logs (for later)
- `openTelemetryTrace.ingest` — push OTLP traces

Copy the token — it starts with `dt0c01.`.

### Step 3: Create a .env file

In `phase-11-dynatrace/app/`, create `.env`:

```bash
DT_ENDPOINT=https://abc12345.live.dynatrace.com/api/v2/otlp
DT_API_TOKEN=dt0c01.xxxxxxxxxxxxxxxx.yyyyyyyyyyyy
ENVIRONMENT=development
```

Replace `abc12345` with your environment ID and the token with your actual token.

> **Security:** `.env` is in `.gitignore`. Never commit API tokens. In production, inject secrets via your CI/CD secret store or a secrets manager.

### Step 4: Restart with Dynatrace enabled

```bash
docker compose down
docker compose up -d --build
```

The API logs should show:

```
Dynatrace OTLP export enabled: https://abc12345.live.dynatrace.com/api/v2/otlp
```

### Step 5: Generate load and verify in Dynatrace

Run `./load.sh` for ~2 minutes. Then in your Dynatrace environment:

**Metrics → Explore metrics** → search for `lumio`.

You should see `lumio.http.requests`, `lumio.http.request.duration`, `lumio.active_requests` and others. These are pushed every 60 seconds via OTLP.

---

## Challenge 3 — Explore distributed traces

Dynatrace receives OpenTelemetry traces from the `OTLPSpanExporter`. The `FlaskInstrumentor` creates a root span for every HTTP request automatically. The manual spans in the code add business context on top.

### Step 1: View traces in Dynatrace

In Dynatrace: **Applications & Microservices → Distributed Traces → Ingested traces**.

Filter by service `lumio-api`. Select any trace for a `/events` request.

You will see:
- **Root span:** `POST /events` — created automatically by FlaskInstrumentor
- **Child span:** `process-event` — created manually in `app.py`
- **Span attributes on the child span:** `event.type`, `lumio.pipeline`

### Step 2: Find all traces for failed events

In the trace search, add a filter:

```
Attribute: error = true
```

This returns only the ~2% of requests that hit the simulated error path. Each trace shows:
- `error.reason` — the specific failure reason (`validation_error`, `schema_mismatch`, or `timeout`)
- The exact timestamp and duration

Compare this to what you can do in Grafana: you can see the *rate* of errors (from metrics) and the *log lines* (from Loki) but you cannot easily correlate a specific request's full execution path. That is what traces add.

### Step 3: Understand the span hierarchy

Open the code at `app.py` and find the `ingest_event` route:

```python
with tracer.start_as_current_span("process-event") as span:
    span.set_attribute("event.type", event_type)
    span.set_attribute("lumio.pipeline", "ingest")
    ...
    if random.random() < 0.02:
        span.set_attribute("error", True)
        span.set_attribute("error.reason", reason)
```

The `with tracer.start_as_current_span(...)` block creates a child span nested under the HTTP root span. Any code inside the `with` block is part of that span. Attributes are key-value metadata — they are indexed and searchable in Dynatrace.

> **Manual vs auto instrumentation:** FlaskInstrumentor handles the root HTTP span automatically. Manual spans are added only where business context matters — what type of event was processed, which pipeline handled it. This is the right division of labour: auto-instrumentation covers the frame, manual spans add meaning.

---

## Challenge 4 — Davis AI: anomaly detection without alert rules

One of Dynatrace's core value propositions is Davis AI — automated anomaly detection that requires no threshold configuration.

### Step 1: Observe automatic baselining

After ~30 minutes of load, Dynatrace establishes a dynamic baseline for `lumio-api`:
- Average request rate
- Normal latency range (median + spread)
- Expected error rate

Navigate to **Services → lumio-api → Service health**.

### Step 2: Trigger an anomaly (simulated)

Modify `load.sh` to send a burst of traffic — change `sleep 0.1` to `sleep 0.01` for 60 seconds, then back.

Watch Dynatrace's Problems feed (bell icon at the top). Davis AI will detect:
- Increased response time (if latency degrades under load)
- Request rate anomaly (sudden spike above baseline)

It correlates these into a single **Problem** rather than multiple separate alerts. This is the key difference from Alertmanager: Alertmanager fires on thresholds you define; Davis fires on deviations from what it learned is normal.

### Step 3: Compare to your Prometheus alerting

In Phase 2 (Alertmanager), alerts fire when a metric crosses a hardcoded threshold. The problems with thresholds:
- Too low → alert fatigue (fires on small, normal variations)
- Too high → misses real incidents until they are severe
- Need maintenance as traffic patterns change (e.g., scaling up doubles normal RPS)

Davis baseline adjusts automatically. A service that normally handles 5 RPS and suddenly handles 50 RPS will trigger an anomaly. The same service after a traffic ramp to 50 RPS as the new normal will not.

---

## Challenge 5 — Build an SLO in Dynatrace

In Phase 2, SLOs are tracked by querying Prometheus and building alert rules against the burn rate. Dynatrace has native SLO objects that track compliance continuously and integrate with Davis AI.

### Step 1: Create a Service-level objective

Navigate to **Service-level objectives → Add new SLO**.

| Field | Value |
|---|---|
| **SLO name** | Lumio API P95 latency |
| **Type** | Service-level indicator (custom) |
| **Metric expression** | `(100)*(builtin:service.response.time.percentile(95) < 200000)` |
| **Target** | 99.5% |
| **Warning** | 99.9% |
| **Timeframe** | Last 7 days |

> The metric `builtin:service.response.time.percentile(95)` is a Dynatrace built-in. Value is in microseconds, so 200ms = 200000µs.

### Step 2: View the SLO status

The SLO dashboard shows:
- **Current compliance** — percentage of time the SLO was met in the window
- **Error budget** — how much headroom remains (e.g., "you can be non-compliant for 43 more minutes this week")
- **Trend** — is compliance improving or degrading?

Davis AI monitors this SLO automatically. If the error budget burn rate accelerates, it creates a Problem before the SLO is missed — not after.

---

## Challenge 6 — Compare the two stacks

You now have both stacks running side by side. Use this challenge to articulate the concrete trade-offs.

### What the self-managed stack (Phases 0–10) gives you

| Capability | How |
|---|---|
| Full data control | Prometheus data stays on your infrastructure |
| No per-host/per-metric pricing | Fixed cost regardless of cardinality |
| Customisable retention | You control how long data is kept |
| Open standards throughout | Any tool that speaks PromQL or OTLP works |
| No vendor lock-in | Migrate backends without re-instrumenting |

### What Dynatrace adds

| Capability | How |
|---|---|
| Automatic topology discovery | No need to configure service maps — Smartscape builds them |
| Davis AI root cause analysis | Finds root cause across metrics + traces + logs + topology |
| Zero-config baselining | No threshold tuning — adapts to traffic changes automatically |
| Native SLO tracking with burn rate | Built-in, not a Prometheus recording rule |
| OneAgent (optional) | Instrument VMs, containers, Kubernetes without code changes |
| Full-stack correlation | Host CPU spike → service latency spike → business metric drop in one Problem |

### What Dynatrace costs you

| Cost | Detail |
|---|---|
| License (SaaS) | Per-host or DPS (Davis Performance Score) pricing — significant at scale |
| Data residency | Telemetry leaves your infrastructure to Dynatrace SaaS |
| Vendor dependency | SLO definitions, alert rules, and dashboards are in Dynatrace's proprietary format |
| Reduced cardinality | Dynatrace ingests high-cardinality OTel data but cost scales with DPS consumption |

### The hybrid pattern (what most large teams do)

```
App instrumentation (OTel SDK)
        │
        ├── OTLP → Dynatrace SaaS          ← Davis AI, full-stack correlation, SLO tracking
        │
        └── Prometheus scrape → Grafana    ← Custom dashboards, cost-efficient long-term storage
```

Keep both. Use Dynatrace for incident investigation and SLO management where its AI adds value. Use Prometheus for custom dashboards and cheap long-term metric storage. OTel instrumentation makes this possible without writing metrics twice.

---

## Challenge 7 — Understand what OTel changed in the code

### Step 1: Compare app.py to Phase 0

Phase 0 used `prometheus_client` directly:
```python
REQUEST_COUNT = Counter('lumio_http_requests_total', ..., ['method', 'endpoint', 'status_code'])
REQUEST_COUNT.labels(method=..., endpoint=..., status_code=...).inc()
```

Phase 11 uses OTel instruments:
```python
request_counter = meter.create_counter("lumio.http.requests", unit="requests")
request_counter.add(1, {"method": ..., "endpoint": ..., "status_code": ...})
```

The API is similar. The difference is where the data goes: prometheus_client only knows about Prometheus; the OTel counter knows nothing about backends — it writes to whatever readers the `MeterProvider` was configured with.

### Step 2: Trace the dual pipeline in code

Find the `MeterProvider` setup in `app.py`:

```python
metric_readers = [prometheus_reader]         # always — Prometheus scrape

if dt_enabled:
    metric_readers.append(otlp_reader)       # optional — push to Dynatrace

meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
```

The `MeterProvider` is the composition root. All instruments (`request_counter`, `request_duration`, etc.) write to it. The readers decide what to do with the data. Adding or removing a backend is a one-line change here, not a change scattered across 20 metric call sites.

### Step 3: Understand the resource

```python
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME:        "lumio-api",
    ResourceAttributes.SERVICE_VERSION:     "1.0.0",
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: ENVIRONMENT,
})
```

The resource is metadata about the source of the telemetry — not a label on individual metrics, but context attached to every metric, trace, and log emitted by this process. In Dynatrace it appears as entity properties. In Prometheus it becomes target labels. In a trace it appears as `service.name` and `service.version` on every span.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start stack (Prometheus-only if no .env) |
| `docker compose down` | Stop and remove containers |
| `docker compose logs -f api` | Stream API logs (shows DT enable/disable message at startup) |
| `curl http://localhost:8000/metrics \| grep lumio` | Inspect OTel-generated Prometheus metrics |

| Dynatrace action | Where |
|---|---|
| Browse metrics | Metrics → Explore metrics |
| View traces | Applications & Microservices → Distributed Traces → Ingested traces |
| Create SLO | Service-level objectives → Add new SLO |
| View Problems | Bell icon → Problems feed |
| Service health | Services → lumio-api → Service health |
| Topology map | Infrastructure → Smartscape |

---

## What this doesn't cover

| Topic | Notes |
|---|---|
| Dynatrace OneAgent | OneAgent is the zero-code instrumentation path for hosts and VMs. In a container lab the OTLP path is more practical and more transferable. OneAgent is covered in Dynatrace's own docs. |
| Kubernetes operator | `dynatrace-operator` deploys OneAgent and the Dynatrace ActiveGate on Kubernetes clusters. Out of scope for a Docker Compose lab. |
| Log forwarding via OTLP | The `logs.ingest` token scope is provisioned but this lab does not configure the OTel log exporter. Adding it follows the same pattern as the metric and trace exporters. |
| Dynatrace Grail | Dynatrace's next-gen data lakehouse for long-term storage and DQL querying. Relevant at enterprise scale. |

---

## Production considerations

### 1. Use OTel SDK for all new instrumentation
Never import both `prometheus_client` and the OTel SDK in the same codebase for the same metrics. Choose OTel and use the Prometheus exporter to expose a scrape endpoint. `prometheus_client` becomes a transitive dependency of `opentelemetry-exporter-prometheus`, not a direct one.

### 2. Set the resource at startup, not per-request
The `Resource` object is expensive to construct (it reads environment variables and merges with auto-detected attributes). Create it once at module level. All metrics, traces, and logs emitted during the process lifetime will carry those attributes.

### 3. Pin OTel package versions together
The OTel Python packages are versioned in two tracks: `opentelemetry-api` and `opentelemetry-sdk` follow `1.x.x`; instrumentation and exporter packages follow `0.x`. They must be pinned together — mixing incompatible versions causes silent export failures. Use a constraints file or lock file and upgrade the entire OTel bundle at once.

### 4. OTLP export interval vs scrape interval
The `PeriodicExportingMetricReader` in this lab is configured with `export_interval_millis=60_000` (1 minute). This means Dynatrace metrics are 1 minute behind Prometheus. For incident response, adjust to `15_000` (15 seconds) to match the Prometheus scrape cadence — at the cost of more API calls and DPS consumption.

### 5. The token is a secret
`DT_API_TOKEN` has write access to your Dynatrace environment's telemetry ingest endpoints. Treat it like a database password. Use Docker secrets, Kubernetes secrets, or your CI secret store — never an environment variable in a Dockerfile or committed `.env`.

### 6. Dynatrace costs scale with DPS
Dynatrace pricing is based on Davis Performance Score units, which scale with the volume of data ingested. High-cardinality OTel metrics (many unique attribute combinations) consume DPS rapidly. Apply the same cardinality discipline from Phase 8 — avoid dynamic attribute values like user IDs, request IDs, or session tokens as span or metric attributes.

---

## Outcome

Lumio now has a dual telemetry pipeline. The OTel SDK is the single instrumentation layer — it emits to Prometheus for dashboards and long-term storage, and to Dynatrace for AI-powered root cause analysis, automatic topology discovery, and SLO tracking.

The architectural decision — OTel as instrumentation, multiple backends as consumers — is the industry standard pattern at companies that operate both a self-managed Prometheus stack and a commercial APM platform. It preserves optionality: if Dynatrace is dropped tomorrow, no application code changes. If Prometheus is replaced, same story.

The two-week trial answered the CTO's question: Dynatrace saved time on incident investigation (Davis AI reduced MTTR by ~40% in the trial period) and eliminated the need to maintain alert rules manually. The cost was approximately equivalent to one FTE. The team committed to the hybrid pattern: Prometheus for dashboards and metrics storage, Dynatrace for incident intelligence.

---

[← Back to Phase 10 — Capstone](../phase-10-capstone/README.md) | [Back to root README](../README.md)
