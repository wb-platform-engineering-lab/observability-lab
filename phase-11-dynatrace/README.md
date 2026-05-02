# Phase 11 — Enterprise APM with Dynatrace

> **Prerequisites:** Phase 3 — OpenTelemetry. Phase 11 assumes the OTel SDK and Collector are already in place.
>
> **Concepts introduced:** Dynatrace OTLP ingest, Collector fan-out, Smartscape topology, Davis AI anomaly detection, Dynatrace SLO management, build-vs-buy analysis

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Collector fan-out** | One Collector pipeline exporting to two or more backends simultaneously | Adding Dynatrace requires a config change, not a code change |
| **Dynatrace OTLP ingest** | Dynatrace SaaS accepting standard OTLP metrics and traces | No proprietary agent or SDK required — the OTel SDK from Phase 3 is enough |
| **Smartscape** | Dynatrace's real-time topology map | Automatic service dependency discovery — no manual configuration |
| **Davis AI** | Dynatrace's AI engine for anomaly detection and root cause analysis | Correlates signals across metrics, traces, topology, and deployments into a single Problem |
| **Dynamic baselining** | Davis learns normal behaviour and alerts on deviations | No threshold to configure — adapts automatically as traffic patterns change |
| **Dynatrace SLO** | Service-level objective defined and tracked in Dynatrace | Native burn rate tracking; Davis alerts when an SLO is at risk before it is missed |
| **Build vs buy** | Choosing between a self-managed OSS stack and a commercial APM platform | The Collector fan-out pattern makes this a reversible decision |

---

## The problem

> *Lumio — 100 engineers. Twelve months after the Phase 10 capstone.*
>
> The full observability platform was running and working. But it had become infrastructure in itself. Two engineers owned it full-time. Prometheus upgrades, Loki schema migrations, Alertmanager config refactors — the stack required continuous maintenance.
>
> The new CTO asked: "At what headcount does it make sense to buy this instead of build it?"
>
> A Dynatrace trial was spun up on a Friday afternoon. Because the team had adopted the OTel Collector in Phase 3, connecting Dynatrace required exactly one change: adding four lines to `otelcol/config.yml`. The application code was not touched.

---

## Architecture

```
phase-11-dynatrace/app/

  ┌──────────────────────────────────────┐
  │  lumio-api (unchanged from Phase 3)  │
  │  sends OTLP gRPC → otelcol:4317      │
  └──────────────────┬───────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────┐
  │  OTel Collector                                  │
  │                                                  │
  │  pipelines:                                      │
  │    traces  → [otlp/tempo, otlphttp/dynatrace]   │
  │    metrics → [prometheus, otlphttp/dynatrace]   │
  └───────────┬──────────────────────────┬───────────┘
              │                          │
       ┌──────▼──────┐          ┌────────▼────────┐
       │  Tempo      │          │  Dynatrace SaaS │
       │  Prometheus │          │  (metrics+traces│
       │  Grafana    │          │  + Davis AI)    │
       └─────────────┘          └─────────────────┘

  Left side:  self-managed OSS stack (always running)
  Right side: Dynatrace (active when DT_ENDPOINT + DT_API_TOKEN are set)
```

**The key architectural point:** The application has not changed. The Collector is the fan-out point. This makes the Dynatrace evaluation reversible — remove the exporter from the Collector config to stop sending data.

---

## What changed between Phase 3 and Phase 11

### `otelcol/config.yml` — four lines added

```yaml
exporters:
  # ... existing exporters unchanged ...

  otlphttp/dynatrace:                              # ← new
    endpoint: ${env:DT_ENDPOINT}                  # ← new
    headers:                                       # ← new
      Authorization: "Api-Token ${env:DT_API_TOKEN}" # ← new

service:
  pipelines:
    traces:
      exporters: [otlp/tempo, otlphttp/dynatrace]  # ← added dynatrace
    metrics:
      exporters: [prometheus, otlphttp/dynatrace]   # ← added dynatrace
```

### `docker-compose.yml` — two environment variables added to the Collector service

```yaml
otelcol:
  environment:
    - DT_ENDPOINT=${DT_ENDPOINT:-https://placeholder...}
    - DT_API_TOKEN=${DT_API_TOKEN:-disabled}
```

### `api/app.py` — unchanged

Zero application code changes. This is the concrete payoff of the Collector pattern from Phase 3.

---

## Challenge 1 — Run the stack without Dynatrace

Before connecting Dynatrace, verify Phase 3's full pipeline still works.

```bash
cd phase-11-dynatrace/app
docker compose up -d --build
chmod +x load.sh && ./load.sh
```

Open **http://localhost:3000** → Dashboards → Lumio. Metrics and traces work via the same Prometheus + Tempo + Grafana stack as Phase 3.

The Collector will log warnings about the Dynatrace exporter failing (the placeholder endpoint is not reachable). This is expected — the Prometheus and Tempo pipelines continue to function regardless.

---

## Challenge 2 — Connect to Dynatrace

### Step 1: Create a free Dynatrace trial

Go to **https://www.dynatrace.com/trial** and sign up (no credit card required, 15-day trial).

You will receive an Environment ID — the subdomain of your SaaS environment (e.g. `abc12345`). Your environment URL is `https://abc12345.live.dynatrace.com`.

### Step 2: Generate an API token

In your Dynatrace environment: **Settings → Access tokens → Generate new token**.

Required scopes:
- `metrics.ingest`
- `openTelemetryTrace.ingest`

Copy the token — it starts with `dt0c01.`.

### Step 3: Create `.env`

In `phase-11-dynatrace/app/`, create `.env`:

```
DT_ENDPOINT=https://abc12345.live.dynatrace.com/api/v2/otlp
DT_API_TOKEN=dt0c01.xxxxxxxxxxxxxxxx.yyyyyyyy
```

> **Security:** `.env` is in `.gitignore`. Never commit API tokens.

### Step 4: Restart the Collector

```bash
docker compose restart otelcol
```

Check the Collector logs for the Dynatrace exporter:

```bash
docker compose logs otelcol | grep -i dynatrace | tail -5
```

A successful export shows `"msg":"Traces Exporter"` without error messages.

### Step 5: Verify in Dynatrace

After ~2 minutes of load:

**Metrics → Explore metrics** → search `lumio`. You should see `lumio.http.requests`, `lumio.http.request.duration`, etc.

**Applications & Microservices → Distributed Traces → Ingested traces** → service `lumio-api`.

---

## Challenge 3 — Smartscape: automatic topology discovery

Navigate to **Infrastructure → Smartscape** in your Dynatrace environment.

After a few minutes of load, Dynatrace builds a topology map automatically. For the Lumio API it shows:
- The `lumio-api` service entity
- Its runtime environment (Docker container)
- The host it runs on

No configuration required. For a multi-service architecture, Smartscape shows which services call which, automatically detected from the trace context propagation headers in OTLP data.

**Compare to Grafana:** Building a service topology map in Grafana requires either Tempo's metrics_generator (generates RED metrics from traces) or manual configuration. Smartscape builds it from the OTLP data with zero configuration.

---

## Challenge 4 — Davis AI: anomaly detection without thresholds

### Step 1: Observe automatic baselining

After 30 minutes of load, navigate to **Services → lumio-api → Service health**.

Dynatrace has built a dynamic baseline:
- Normal request rate range
- Normal latency range (with expected spread)
- Expected error rate

### Step 2: Trigger a simulated anomaly

Increase traffic by changing `sleep 0.1` to `sleep 0.01` in `load.sh` for 60 seconds, then revert.

In **Problems** (bell icon): Davis will detect the traffic rate anomaly and create a Problem automatically — no threshold was configured, no alert rule was written.

### Step 3: Compare to Phase 2 alerting

| | Alertmanager (Phase 2) | Davis AI (Phase 11) |
|---|---|---|
| How it fires | Metric crosses a hardcoded threshold | Deviation from learned baseline |
| Threshold maintenance | Manual — needs updating as traffic scales | Automatic — baseline adjusts with traffic |
| Alert grouping | Multiple alerts from the same incident | One Problem per incident, correlated |
| Root cause | Manual investigation across dashboards | Davis identifies root cause automatically |
| False positive control | Tune `for:` duration and threshold values | Sensitivity slider in Dynatrace settings |

Neither is strictly better. Threshold-based alerts are more predictable — you know exactly when they fire. Davis AI fires on patterns you did not think to threshold. Most mature teams use both: threshold-based alerts for well-understood failure modes (error rate > 5%, disk > 90%), Davis AI for unknown patterns and automatic correlation.

---

## Challenge 5 — Build a Dynatrace SLO

In Dynatrace: **Service-level objectives → Add new SLO**.

| Field | Value |
|---|---|
| **Name** | Lumio API P95 latency |
| **Metric expression** | `(100)*(builtin:service.response.time.percentile(95) < 200000)` |
| **Target** | 99.5% |
| **Warning** | 99.9% |
| **Timeframe** | Last 7 days |

> `builtin:service.response.time.percentile(95)` is in microseconds. 200ms = 200000µs.

The SLO dashboard shows current compliance, remaining error budget (in minutes), and burn rate trend. Davis AI monitors this SLO automatically — if the burn rate accelerates, it creates a Problem before the SLO window closes.

**Compare to Phase 3 SLOs:** In Prometheus, SLO tracking requires recording rules for error budgets and Alertmanager rules for burn rate. In Dynatrace, the SLO object handles all of this. The trade-off is that Dynatrace SLOs are in Dynatrace's format — not portable to another platform.

---

## Challenge 6 — Build vs buy analysis

You now have both stacks running simultaneously. Both receive the same telemetry. Use this challenge to make the comparison concrete.

### Cost of the self-managed stack

| Item | Estimate |
|---|---|
| Prometheus + Loki + Tempo (2 × m5.large) | ~€200/month |
| Engineering maintenance (0.5 FTE) | ~€5,000/month |
| Alert rule tuning, upgrades, migrations | Included in above |
| **Total** | **~€5,200/month** |

At 100 engineers, the engineering cost dominates. Infrastructure is cheap; attention is not.

### Cost of Dynatrace

Dynatrace pricing is based on **DPS (Davis Performance Score)** units, which scale with the volume and richness of the telemetry ingested. A rough estimate for a single moderate-traffic service:

| Item | Estimate |
|---|---|
| DPS consumption for lumio-api at ~10 RPS | ~€300–600/month |
| Engineering maintenance (0.1 FTE) | ~€1,000/month |
| **Total** | **~€1,300–1,600/month** |

At low scale, the self-managed stack is cheaper. At high scale (many services, many engineers), the engineering maintenance cost of the self-managed stack grows faster than Dynatrace's DPS cost.

### The decision framework

```
< 5 services, < 20 engineers:     Self-managed stack — lower cost, higher learning value
5–20 services, 20–100 engineers:  Hybrid — OTel SDK + Prometheus + Dynatrace for incidents
> 20 services, > 100 engineers:   Evaluate full Dynatrace or similar, measure FTE savings
```

### The hybrid pattern (what most large teams run)

```
OTel SDK (instrumentation — vendor-neutral, in the app)
        │
        ▼
OTel Collector
        ├── → Dynatrace SaaS    ← Davis AI, full-stack correlation, SLO tracking
        └── → Prometheus        ← Custom dashboards, cheap long-term metric storage
```

Keep both. Dynatrace for investigation and SLO management where its AI adds value. Prometheus for custom dashboards and long-term cheap storage. The Collector makes this possible without writing metrics twice.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start stack |
| `docker compose restart otelcol` | Reload Collector config (after editing config.yml or .env) |
| `docker compose logs -f otelcol \| grep -i dynatrace` | Watch Dynatrace export status |

| Dynatrace action | Where |
|---|---|
| Explore metrics | Metrics → Explore metrics |
| View ingested traces | Applications & Microservices → Distributed Traces → Ingested traces |
| Topology map | Infrastructure → Smartscape |
| Problems / anomalies | Bell icon → Problems |
| Create SLO | Service-level objectives → Add new SLO |

---

## Production considerations

### 1. Disable the Dynatrace exporter at the Collector level, not the app level
If Dynatrace is unavailable or you want to pause ingestion, remove the exporter from the Collector pipeline. The application continues running unchanged. Removing the exporter at the app level requires a redeployment.

### 2. DPS consumption scales with cardinality
Dynatrace pricing scales with the amount and richness of data ingested. Apply the same cardinality discipline from Phase 8 — avoid high-cardinality attributes (user IDs, session tokens, request IDs) on metrics and spans. The cost impact in Dynatrace is more direct and immediate than in Prometheus.

### 3. The token is a production secret
`DT_API_TOKEN` has write access to your Dynatrace environment. Rotate it on a schedule. Never put it in a Dockerfile ENV, committed `.env`, or application logs. Pass it via Docker secrets, Kubernetes secrets, or your secrets manager.

### 4. Run the Collector with redundancy
The Collector is now a dependency for both Prometheus and Dynatrace. If it goes down, both pipelines stop. In production: run multiple Collector replicas behind a load balancer, or use the sidecar pattern (one Collector per pod) to eliminate the shared point of failure.

---

## Outcome

Dynatrace was added to the Lumio observability stack without a single line of application code changing. The OTel Collector acted as the fan-out point: the same telemetry that feeds Prometheus and Tempo also feeds Dynatrace's AI engine.

The 15-day trial answered the CTO's question: Davis AI reduced MTTR by cutting the time from alert to root cause identification. The SLO management was simpler than maintaining Prometheus recording rules. The cost was equivalent to eliminating one FTE of observability maintenance. The team kept both stacks — Prometheus for dashboards and long-term storage, Dynatrace for incident intelligence.

The decision was reversible because the instrumentation layer (OTel SDK + Collector) was vendor-neutral from Phase 3 onwards.

---

[← Back to Phase 10 — Capstone](../phase-10-capstone/README.md) | [Back to root README](../README.md)
