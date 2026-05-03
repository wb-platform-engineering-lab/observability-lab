# Phase 10 — Capstone: Production Platform

> **Operational story:** Lumio is production. You are on-call.  
> This phase combines every signal from Phases 0–9 into one unified stack and introduces the final concept: **Service Level Objectives (SLOs)** — the formal contract between engineering and the business.

---

## What you will build

A single `docker compose up` brings up the complete Lumio production platform:

| Signal | Source | Pipeline |
|--------|--------|----------|
| Metrics | `prometheus_client` in the API | Prometheus scrapes → recording rules → Alertmanager |
| Traces  | OTel SDK (traces only) | OTLP → otelcol → Tempo |
| Logs    | JSON structured logs | promtail → Loki |

Three Grafana dashboards tie it together:

- **Lumio — Production Platform** (`/d/lumio-platform`) — all golden signals on one page with drill-down links
- **Lumio — SLO Detail** (`/d/lumio-slo`) — error budget, burn rate at four windows, SLI trend
- **Lumio — Infrastructure** (pulled from Phase 7 rules) — visible in the platform dashboard

New concepts introduced in this phase:
- **SLO / SLI / Error budget** — the maths behind reliability targets
- **Burn rate** — how fast the error budget is being consumed
- **Multi-window burn rate alerts** — why you need both a short and a long window
- **Three-signal correlation** — jumping from a metric spike → logs → trace

---

## Prerequisites

- Docker Desktop with at least 4 GB RAM allocated
- Phases 0–9 completed (concepts referenced, not required to be running)
- Ports free: 3000, 8000, 9090, 9091, 9100, 3100, 4317, 14268

---

## Architecture

```
                  ┌─────────────────────────────────────────────────────┐
                  │                  lumio-api (:8000)                  │
                  │  prometheus_client metrics ─────────────────────┐  │
                  │  OTel SDK (traces) ──► otelcol ──► Tempo (:3200) │  │
                  │  JSON logs ──► promtail ──► Loki (:3100)         │  │
                  └───────────────────────────────┬─────────────────-┘  │
                                                  │ scrape /metrics      │
                  ┌───────────────────────────────▼──────────────────┐  │
                  │  Prometheus (:9090)                               │  │
                  │  recording rules: lumio + SLO + infra             │  │
                  │  alerting rules:  lumio + SLO burn rate           │  │
                  └───────────────────────────────┬──────────────────┘  │
                                                  │ alerts               │
                  ┌───────────────────────────────▼──────────────────┐  │
                  │  Alertmanager (:9093)                             │  │
                  │  SLO-aware routing: fast-burn → 15m repeat       │  │
                  └───────────────────────────────┬──────────────────┘  │
                                                  │ webhook              │
                  ┌───────────────────────────────▼──────────────────┐  │
                  │  webhook receiver (:5001)                         │  │
                  └──────────────────────────────────────────────────┘  │
                                                                         │
                  ┌──────────────────────────────────────────────────┐  │
                  │  Grafana (:3000)                                  │  │
                  │  datasources: prometheus, loki, tempo             │  │
                  │  dashboards: platform, slo                        │  │
                  └──────────────────────────────────────────────────┘  │
```

---

## Quick start

```bash
cd phase-10-capstone/app
docker compose up -d
```

Wait ~30 s for all services to initialise, then open Grafana:

```
http://localhost:3000
```

Default credentials: `admin` / `admin`

Generate traffic:

```bash
./load.sh
```

---

## SLO primer

Before working through the challenges, understand the maths:

| Term | Definition | Lumio value |
|------|-----------|-------------|
| **SLO** | Service Level Objective — the reliability target | 99.5% availability over 30 days |
| **SLI** | Service Level Indicator — the measured value | fraction of HTTP requests that return non-5xx |
| **Error budget** | How much downtime is allowed: `(1 − SLO) × window` | `0.005 × 30d = 216 minutes` |
| **Burn rate** | How fast the budget is being consumed: `error_rate / 0.005` | `1×` = on track; `14.4×` = gone in 2h |

### Multi-window alerts

A single short window is noisy — a 30-second spike can look catastrophic.  
A single long window is slow — a sustained incident takes hours to appear.

The fix: require **both** a short window and a long window to exceed the threshold.

| Alert | Condition | Meaning |
|-------|-----------|---------|
| `SLOFastBurn` | 5m > 14.4× **and** 1h > 14.4× | Page immediately — budget gone in 2h |
| `SLOSlowBurn` | 30m > 6× **and** 6h > 6× | File a ticket — budget gone in 5d |

---

## Challenges

### Challenge 1 — Bring up the full stack and verify all three signals

Start the platform and confirm each signal pipeline is healthy before generating any load.

**Metrics**

```bash
curl http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -E '"health"|"job"'
```

All four jobs (`lumio-api`, `node`, `cadvisor`, `prometheus`) should be `"health": "up"`.

**Traces**

Open Grafana → Explore → datasource: Tempo → click **Search** → run a search.  
You should see an empty result (no traffic yet) but no connection error.

**Logs**

```bash
# Confirm promtail is shipping
curl -s http://localhost:9080/targets | grep -c "ready"
```

Generate a few requests and check Loki:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ingest
```

Grafana → Explore → datasource: Loki → query: `{service="api"}` → you should see JSON log lines.

---

### Challenge 2 — Understand the SLI and burn rate recording rules

Open the Prometheus Rules page at `http://localhost:9090/rules` and find the `lumio.slo.recording` group.

**Questions to answer:**

1. What is the PromQL expression for `job:lumio_slo_availability:rate1h`?  
   Hint: it counts 5xx responses over 1h divided by total requests.

2. How is `job:lumio_slo_burn_rate:rate1h` derived from the availability SLI?  
   Hint: the divisor `0.005` is `1 − 0.995`.

3. Run these queries in the Prometheus UI and compare the values:
   ```
   job:lumio_slo_availability:rate5m{job="lumio-api"}
   job:lumio_slo_availability:rate1h{job="lumio-api"}
   ```
   Why do they differ at steady state?

**Verify in Grafana:**

Open `/d/lumio-slo`. With only `./load.sh` running and `ERROR_RATE=0.01`, you should see:
- Availability SLI stats all ≥ 0.99 (green)
- Burn rate 1h stat ≈ 2× (yellow)

---

### Challenge 3 — Trigger SLOFastBurn

Inject a 50% error rate:

```bash
./break.sh fast
```

Keep the SLO dashboard open at `http://localhost:3000/d/lumio-slo`.

Watch:
1. The 5m burn rate panel climb above 14.4× (red) within ~1 minute
2. The 1h burn rate panel climb — it lags because it's a 1h window
3. The `SLOFastBurn` alert fire once both windows exceed 14.4× — watch Prometheus Alerts at `http://localhost:9090/alerts`
4. The webhook receive a notification: `docker compose logs -f webhook`

**The two-window delay is intentional.**

The 5m burn rate reacts immediately to the spike. The 1h burn rate takes time to rise because it's a weighted average over 60 minutes. The alert requires both — so it fires when the incident is confirmed as sustained, not just a brief burst.

Restore normal traffic:

```bash
./break.sh stop
```

Notice how the 5m burn rate drops back within 5 minutes, but the 1h burn rate takes much longer to recover.

---

### Challenge 4 — Trigger SLOSlowBurn

The slow burn is subtler — it's the alert that fires before a problem becomes a crisis.

```bash
./break.sh slow   # 10% error rate
```

With `ERROR_RATE=0.10`, the error rate is 10%.  
Burn rate = `0.10 / 0.005` = **20×** — above the fast burn threshold.

Wait, 20× should trigger fast burn too? Yes — but the **30m and 6h windows** start below the threshold and climb slowly. The `SLOSlowBurn` alert uses 30m + 6h windows, so it fires as those windows accumulate enough samples.

**Timeline with 10% errors:**
- t+1m: 5m burn rate ≈ 20× (above threshold)
- t+5m: 30m burn rate starts climbing
- t+15m: `SLOSlowBurn` fires if 30m > 6 (it will be, at ~20×)
- t+20m: webhook notification arrives

Restore:

```bash
./break.sh stop
```

---

### Challenge 5 — Correlate across all three signals

This challenge is about the full observability workflow: **alert → dashboard → logs → trace**.

1. Trigger the fast burn again: `./break.sh fast`

2. Open the platform dashboard at `/d/lumio-platform`  
   Identify the **Error Rate** stat panel turning red.

3. Click into the **Error Logs (live)** panel.  
   You should see `WARNING`-level log lines. Expand one — find the `trace_id` field.

4. Copy the `trace_id`. Open Grafana → Explore → datasource: Tempo.  
   Paste the trace ID into the search box. Find the failing span.

5. Now work backwards: which endpoint generated the most errors?  
   Check the **Request Rate by Endpoint** panel on the platform dashboard.

6. Cross-reference with Loki:  
   Grafana → Explore → Loki → `{service="api"} | json | level="WARNING"`  
   Filter by `endpoint` — does the log volume match the Prometheus panel?

Restore: `./break.sh stop`

---

### Challenge 6 — Infrastructure visibility

With the platform running and `./load.sh` sending traffic, explore the infrastructure panels on the platform dashboard.

**Container resources** (bottom of the platform dashboard):

1. Which container consumes the most CPU?
2. Which container has the highest memory footprint?

**Predicted disk fill:**

The `NodeDiskFilling` alert in `infra_alerting.yml` uses `predict_linear`:

```promql
predict_linear(
  instance_mountpoint:node_filesystem_avail_bytes:current{mountpoint="/"}[1h],
  4 * 3600
) < 0
```

Explain in your own words: what does this alert fire on?  
Hint: the second argument to `predict_linear` is a future time horizon in seconds.

**Memory pressure simulation** (optional):

```bash
# Allocate ~500 MB in a temporary container
docker run --rm -it --memory=512m python:3.11-slim python3 -c "
x = bytearray(400 * 1024 * 1024)  # 400 MB
input('Press Enter to release...')
"
```

Watch `instance:node_memory_utilization:ratio` in the Prometheus UI.  
Does the Host Memory panel in Grafana update within 15 seconds?

---

### Challenge 7 — Alertmanager routing and inhibition

Open the Alertmanager UI at `http://localhost:9093`.

**Routing tree inspection:**

Look at the active routes in `alertmanager/alertmanager.yml`.

1. Which route handles `SLOFastBurn`?  
   Hint: it has `slo = availability` and `severity = critical` matchers.

2. What is the `repeat_interval` for SLO critical alerts? Why is it shorter than the default 4h?

3. What does the inhibit rule do?  
   ```yaml
   source_matchers: [alertname = LumioServiceDown]
   target_matchers: [alertname =~ "LumioHigh.*|LumioElevated.*|SLO.*"]
   equal: [job]
   ```

**Test the inhibit rule:**

Stop the API container:

```bash
docker compose stop api
```

Wait 1 minute for `LumioServiceDown` to fire.  
Now check the Alertmanager UI — are the SLO alerts also suppressed?

This is the inhibition in action: if the service is completely down, all the derivative alerts (high error rate, SLO burn) are noise. The `LumioServiceDown` alert is the root cause — inhibit the rest.

Restore: `docker compose start api`

---

### Challenge 8 — End-to-end runbook simulation

Treat this as a real on-call exercise.

**Scenario:** You receive a Slack message (webhook notification) at 14:32:  
*"SLOFastBurn firing — severity critical — burn rate 1h=18×"*

Work through the following runbook steps using only what's available in this stack:

1. **Triage** — Is the service up? Check `up{job="lumio-api"}` in Prometheus.  
   Is this a full outage or elevated error rate?

2. **Scope** — Which endpoint is responsible?  
   `job_endpoint:lumio_http_requests_total:rate5m{job="lumio-api"}`

3. **Logs** — Find the first ERROR log line that appeared.  
   Loki: `{service="api"} | json | level="WARNING"` sorted descending, look for the oldest entry in the incident window.

4. **Trace** — Pick a trace ID from the log and open it in Tempo.  
   Which span is slow or erroring?

5. **Burn rate impact** — How much error budget has been consumed?  
   `job:lumio_slo_error_budget_remaining:approx6h{job="lumio-api"}`

6. **Mitigate** — Run `./break.sh stop` to simulate deploying a fix.

7. **Confirm recovery** — Watch the burn rate drop. How long until the 1h window clears?

Write down your answers as if filing a post-incident review. This is the workflow the whole lab was building towards.

---

## Service endpoints

| Service | URL |
|---------|-----|
| Lumio API | http://localhost:8000 |
| API metrics | http://localhost:8000/metrics |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Grafana | http://localhost:3000 |
| Loki | http://localhost:3100 |
| Tempo | http://localhost:3200 |
| otelcol health | http://localhost:13133 |
| Prometheus targets | http://localhost:9090/targets |
| Prometheus rules | http://localhost:9090/rules |
| TSDB status | http://localhost:9090/tsdb-status |

---

## Grafana dashboards

| Dashboard | UID | Purpose |
|-----------|-----|---------|
| Lumio — Production Platform | `lumio-platform` | All signals — golden signals, logs, infra, containers |
| Lumio — SLO Detail | `lumio-slo` | Error budget, burn rate all windows, SLI trend |

The platform dashboard links to the SLO dashboard and to Loki/Tempo Explore via the top navigation links.

---

## Key files

```
phase-10-capstone/app/
├── api/
│   ├── app.py                          # prometheus_client + OTel traces + JSON logs
│   └── requirements.txt
├── otelcol/
│   └── config.yml                      # traces pipeline only
├── promtail/
│   └── promtail.yml                    # ships JSON logs to Loki
├── prometheus/
│   ├── prometheus.yml                  # external_labels: {env: prod}
│   └── rules/
│       ├── lumio_recording.yml         # golden signal recording rules
│       ├── lumio_alerting.yml          # app alerts (service down, error rate, latency)
│       ├── slo_recording.yml           # SLI + burn rate at 4 windows + error budget
│       ├── slo_alerting.yml            # SLOFastBurn + SLOSlowBurn
│       ├── infra_recording.yml         # node + cAdvisor aggregates (from Phase 7)
│       └── infra_alerting.yml          # disk, memory, CPU alerts (from Phase 7)
├── alertmanager/
│   └── alertmanager.yml               # SLO-aware routing + inhibit on service down
├── grafana/
│   └── dashboards/
│       ├── lumio-platform.json         # unified overview
│       └── lumio-slo.json              # SLO deep-dive
├── docker-compose.yml                  # 11-service full stack
├── load.sh                             # generates realistic traffic
└── break.sh                            # injects failures to test SLO alerts
```

---

## Concepts introduced

| Concept | Where to see it |
|---------|----------------|
| SLO / SLI / Error budget | `prometheus/rules/slo_recording.yml` |
| Burn rate calculation | `job:lumio_slo_burn_rate:*` recording rules |
| Multi-window burn rate alerts | `prometheus/rules/slo_alerting.yml` |
| Two-window alert logic | Challenge 3 |
| Three-signal correlation | Challenge 5 |
| Alert inhibition on service down | `alertmanager/alertmanager.yml` inhibit_rules |
| `external_labels` for env tagging | `prometheus/prometheus.yml` |
| OTel + prometheus_client hybrid | `api/app.py` |
| `predict_linear` disk alerts | `prometheus/rules/infra_alerting.yml` |

---

## Teardown

```bash
docker compose down -v
```

The `-v` flag removes the named volumes (Prometheus TSDB, Loki chunks, Tempo traces).  
Omit it if you want to preserve data between sessions.
