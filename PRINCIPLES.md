# Observability Principles — Before You Write a Single PromQL Query

> Read this before Phase 0. The phases teach you *how* to do things. This document explains *why* — and what you should have decided before you start.

---

## What observability is (and what it isn't)

Observability is the ability to understand what is happening inside a system by examining its external outputs. A system is observable if you can answer any question about its internal state without deploying new code.

The term comes from control theory. In engineering, a system is observable if its internal state can be determined from its outputs alone. Applied to software: you should be able to understand why your system is behaving the way it is — from the data it already emits — without adding a print statement, SSHing into a host, or restarting a process.

**Observability is not monitoring.**

Monitoring is asking known questions about a known system: "Is the CPU above 80%?" "Is the endpoint returning 200?" Monitoring is useful, but it can only detect failure modes you anticipated. It tells you *that* something is wrong.

Observability lets you ask unknown questions about an unknown failure. It tells you *why* something is wrong — even if you have never seen that failure mode before.

```
Monitoring:
  "Is request latency above 500ms?"   → yes/no
  "Is the error rate above 1%?"       → yes/no

Observability:
  "Why did checkout latency spike at 2:14am only for customers in France?"
  "Which specific dependency call is responsible for the P99 regression?"
  "Is this correlated with the deployment that happened at 2:10am?"
```

The first requires pre-defined thresholds. The second requires rich, high-cardinality data and the ability to slice and correlate it freely.

**What observability is not:**

- It is not buying a monitoring tool and calling it done. Tooling is 20% of the work.
- It is not replacing your on-call rotation. It is giving that rotation the information they need to respond effectively.
- It is not a one-time project. A system that is observable today may become opaque as it evolves. Instrumentation must be maintained alongside the code.
- It is not only for incidents. The most valuable use of observability data is proactive: understanding normal behaviour before something breaks.

---

## The three pillars

Every observable system emits three types of telemetry. They are complementary — each answers different questions.

### 1. Metrics

Numerical measurements sampled over time. Metrics are aggregated — they tell you *how much* and *how fast*, but not *why*.

```
lumio_http_requests_total{endpoint="ingest_event", status_code="503"} = 142

rate(lumio_http_requests_total{status_code="503"}[5m]) = 0.47/s

histogram_quantile(0.95, rate(lumio_http_request_duration_seconds_bucket[5m])) = 0.183s
```

Metrics are cheap to store (one number per time window), fast to query, and alertable. They tell you *something is wrong* and roughly *where*. They cannot tell you *why*.

**This lab:** Phases 0–3, 6–10 (Prometheus + Grafana)

### 2. Logs

Timestamped records of discrete events. Logs are detailed — they tell you *what happened*, with context, but they are expensive to store and slow to query at scale.

```
2026-05-02T02:14:33Z ERROR upstream_timeout customer_id=cust_9821 event_type=checkout latency_ms=5043
2026-05-02T02:14:33Z ERROR upstream_timeout customer_id=cust_4417 event_type=checkout latency_ms=5021
2026-05-02T02:14:34Z ERROR upstream_timeout customer_id=cust_1190 event_type=cart_add  latency_ms=5018
```

Logs answer *why*: the upstream payment service started timing out at 02:14:33 — exactly when the Prometheus spike started.

**This lab:** Phases 4–5 (Loki + Promtail)

### 3. Traces

Records of a request's journey across multiple services, with timing for each step. Traces answer *where in the call chain* the time was spent.

```
checkout request  [total: 4.9s]
  ├── auth service        [ 12ms]
  ├── inventory check     [ 45ms]
  ├── payment gateway     [4.8s]  ← here
  └── confirmation email  [  8ms]
```

Without traces, you know checkout is slow. With traces, you know it is the payment gateway call, specifically the `POST /charge` request.

**This lab:** Referenced in Phase 5 (correlation); full tracing is a future extension.

---

## The three observability methods

Before building dashboards, choose a method. A method defines which questions your observability stack must answer and therefore which metrics to instrument.

### RED — for request-driven services

The minimum viable signal set for any service that handles requests:

| Signal | Question | Metric |
|---|---|---|
| **R**ate | How many requests per second? | `rate(requests_total[5m])` |
| **E**rror rate | What percentage are failing? | `rate(requests_total{status=~"5.."}[5m]) / rate(requests_total[5m])` |
| **D**uration | How long do they take? | `histogram_quantile(0.95, rate(duration_bucket[5m]))` |

RED was introduced by Tom Wilkie at Weaveworks. It answers the most common incident question: *"Is this service healthy, and if not, which dimension is failing?"*

**When to use RED:** Any API, microservice, queue consumer, or batch job that processes discrete requests or jobs.

### USE — for infrastructure and resources

For anything that has a limited capacity:

| Signal | Question | Metric |
|---|---|---|
| **U**tilisation | How much of the resource is in use? | CPU: `rate(cpu_seconds_total[5m])` |
| **S**aturation | How much work is queued waiting? | Queue depth, run queue length |
| **E**rrors | How often is it failing? | Disk errors, network drops |

USE was defined by Brendan Gregg. It answers: *"Is this resource a bottleneck?"*

**When to use USE:** Hosts, databases, load balancers, network interfaces, storage systems — anything with a utilisation ceiling.

### Four Golden Signals — for user-facing systems

From Google's SRE Book. Extends RED with a saturation signal:

| Signal | What it measures |
|---|---|
| **Latency** | How long requests take — distinguish successful vs failed latency |
| **Traffic** | Demand on the system (RPS, events/s, concurrent users) |
| **Errors** | Rate of failed requests — distinguish explicit (5xx) from implicit (wrong result) |
| **Saturation** | How "full" the service is — queue depth, thread pool usage, memory pressure |

**When to use:** Customer-facing products where a degraded user experience is the primary failure mode to detect.

> **This lab uses RED as its primary method.** It is the simplest complete model for the Lumio API. USE is introduced in Phase 7 (infrastructure metrics). The Four Golden Signals are the conceptual umbrella that encompasses both.

---

## The seven observability principles

### 1. Instrument at the source

Observe the application itself, not the infrastructure wrapper. A load balancer can tell you that requests are slow; it cannot tell you which internal function is responsible. A Kubernetes node exporter can tell you memory is high; it cannot tell you which query is causing it.

The application is the only place with full context: the customer, the event type, the database query, the upstream dependency call. Instrument there.

**Implemented in:** Phase 0 (prometheus_client in the Flask app), Phase 3 (custom business metrics).

### 2. Structure your data

Unstructured logs and untagged metrics are a liability at scale.

A log line like `ERROR: something failed` requires a human to read every line to understand frequency and context. A structured log line like:

```json
{"level":"error","reason":"upstream_timeout","endpoint":"checkout","customer_segment":"enterprise","latency_ms":5043}
```

can be queried: *"How many upstream timeouts for enterprise customers on the checkout endpoint in the last 15 minutes?"*

The same applies to metrics: a counter named `errors_total` with no labels is a single number. A counter named `errors_total{endpoint, reason, customer_segment}` is a queryable, filterable dataset.

**Implemented in:** Phase 0 (labelled metrics), Phase 4 (structured log format for Loki).

### 3. Alert on symptoms, not causes

A CPU alert at 80% tells you a resource is high. It does not tell you whether a customer is affected. A CPU at 95% may be perfectly acceptable during a planned batch job.

An alert on "checkout error rate > 1% for 5 minutes" tells you customers cannot complete purchases right now. Every engineer who sees it understands the impact without needing system internals knowledge.

| Alert type | Example | Problem |
|---|---|---|
| Cause-based | `cpu > 80%` | May fire when nothing is wrong for the user |
| Cause-based | `memory > 70%` | Fires constantly on JVM applications by design |
| Symptom-based | `checkout error rate > 1%` | Only fires when users are affected |
| Symptom-based | `P95 latency > 500ms for 10 min` | User-visible degradation, actionable |

**Implemented in:** Phase 2 (Alertmanager rules), Phase 3 (SLO-based alerting).

### 4. Define SLIs and SLOs before writing dashboards

A Service Level Indicator (SLI) is a carefully defined quantitative measure of service quality. A Service Level Objective (SLO) is a target value for that SLI.

Without SLOs, dashboards are decoration. You cannot answer *"is this metric in a good or bad state?"* without knowing what good looks like.

Define before you build:

| SLI | SLO |
|---|---|
| Availability (% of successful requests) | 99.9% over a 28-day window |
| Latency (% of requests completing < 200ms) | 95% of requests |
| Event processing success rate | 99.5% of submitted events |

SLOs define the thresholds for your alerts, the red lines on your dashboards, and the burn rate calculations for your error budgets.

**Implemented in:** Phase 3 (SLO dashboards and burn rate alerts).

### 5. Cardinality is a resource — spend it deliberately

Every unique combination of label values creates a separate time series in Prometheus. Ten label values with ten options each = 10^10 time series. That will OOM Prometheus before the end of the day.

Labels are powerful. They are also the primary scaling risk of Prometheus.

**Safe labels:** `endpoint`, `method`, `status_code`, `region`, `environment` — small, fixed, finite sets of values.

**Dangerous labels:** `user_id`, `request_id`, `session_token`, `email`, `ip_address` — unbounded, high-cardinality values that create a unique time series per value.

```python
# ✓ correct — finite set of values
REQUEST_COUNT.labels(endpoint='checkout', status_code='200').inc()

# ✗ dangerous — unlimited unique time series
REQUEST_COUNT.labels(customer_id=customer_id).inc()  # never do this
```

**Implemented in:** Phase 0 (careful label design), Phase 8 (cardinality monitoring and protection).

### 6. Dashboards are for humans, not for Prometheus

Prometheus answers raw questions. Dashboards translate those answers into decisions.

A good dashboard:
- Tells you immediately whether the service is healthy (no PromQL knowledge required)
- Shows the three RED signals prominently
- Uses colour thresholds so "green = ok, red = investigate" without reading numbers
- Has variables for drilling down (by endpoint, by customer segment, by deployment)
- Is stored as code, reviewed in PRs, and deployed via provisioning

A bad dashboard:
- Shows 40 panels of raw counter values
- Has no thresholds or context
- Was built by one person in the UI and exists nowhere in git
- Requires knowing the metric names to interpret

**Implemented in:** Phase 1 (Grafana dashboards, provisioning, dashboard as code).

### 7. Correlate across pillars

The three pillars are most powerful when used together. The typical incident workflow:

```
1. Alert fires (metrics):  checkout error rate crossed 1%
   ↓
2. Open dashboard (metrics): spike started at 02:14, errors on /checkout only
   ↓
3. Jump to logs (Loki):    find the log lines at 02:14 — upstream_timeout, payment service
   ↓
4. Check traces (optional): confirm it is the POST /charge call timing out at 4.8s
   ↓
5. Resolution:             payment gateway had a brief outage; requeue or compensate
```

Without log correlation, step 3 requires SSHing into a host and running grep. Without metrics, you might never notice step 1 at all. The stack only delivers its full value when all three pillars are in place and linked.

**Implemented in:** Phase 5 (log-metric correlation with Grafana Explore).

---

## Before you write a single PromQL query: what to decide first

These are not technical decisions. They are architectural and organisational decisions that shape everything that follows. Getting them wrong after the fact means rebuilding your entire observability stack.

### 1. What are your SLIs?

Define the quantities that directly reflect your users' experience of the service. For the Lumio API:

| SLI | Definition | Measurement |
|---|---|---|
| Availability | Fraction of requests that succeed | `rate(requests{status!~"5.."}[5m]) / rate(requests[5m])` |
| Latency | Fraction of requests completing under threshold | `rate(duration_bucket{le="0.2"}[5m]) / rate(duration_count[5m])` |
| Throughput | Events successfully processed per second | `rate(events_processed_total[5m])` |

SLIs must be measurable from existing telemetry. If you cannot measure an SLI today, instrument for it before defining an SLO.

### 2. What are your SLO targets?

An SLO without a window and a target is not an SLO — it is a wish.

| SLI | SLO | Window |
|---|---|---|
| Availability | 99.9% | 28 days |
| Latency (P95 < 200ms) | 95% of requests | 28 days |
| Event processing success | 99.5% | 7 days |

The SLO window determines your error budget. A 99.9% availability SLO over 28 days gives you 40 minutes of downtime budget. When the budget is exhausted, you stop shipping features until reliability is restored.

### 3. What is your alert routing strategy?

Before configuring a single alert, decide:

| Decision | Options |
|---|---|
| **Who gets paged at 3am?** | Specific team, rotating oncall, escalation policy |
| **What channels?** | PagerDuty, Slack, email, webhook |
| **What severity levels exist?** | Critical (page now) / Warning (notify) / Info (log only) |
| **What is the deduplication window?** | How long before a repeat alert fires again? |
| **What inhibition rules exist?** | If the API is down, suppress endpoint-level alerts |

Alert routing defined after the fact means every alert fires to the same Slack channel until someone manually cleans it up. That is where alert fatigue comes from.

### 4. What is your retention strategy?

| Tier | Tool | Duration | Use case |
|---|---|---|---|
| Hot (queryable) | Prometheus local storage | 15–30 days | Operational dashboards, recent incidents |
| Warm (compressed) | Thanos / Mimir object store | 1–2 years | Post-incident analysis, capacity planning |
| Cold (archive) | S3 / GCS | Indefinite | Compliance, year-over-year comparisons |

Prometheus's default 15-day retention is not a long-term store. If you need to answer *"how does this month's latency compare to three months ago?"*, you need a remote storage backend before the data ages out.

### 5. What is your label taxonomy?

Define label names and allowed values before you write the first metric. Inconsistent labels across services make federation and cross-service dashboards impossible.

| Label | Allowed values | Notes |
|---|---|---|
| `service` | `lumio-api`, `lumio-worker`, `lumio-scheduler` | Service name — consistent across all services |
| `environment` | `dev`, `staging`, `production` | Never mix environments in one Prometheus instance |
| `endpoint` | URL path, normalised | Remove path parameters: `/users/:id` not `/users/12345` |
| `status_code` | `200`, `201`, `400`, `503` etc. | Use the actual code, not a class |

---

## Architecture decisions

### Decision 1 — Prometheus deployment model

**The question:** Where does Prometheus run, and what does it scrape?

```
Option A: Single Prometheus per environment
  + Simple: one place to query, one config to maintain
  + Suitable for small to medium deployments
  − Single point of failure
  − Does not scale beyond ~10M active time series

Option B: Federation (hierarchical Prometheus)
  + Each team/service runs their own Prometheus
  + A top-level Prometheus federates summary metrics
  − Complex routing; federation queries are expensive
  − Partial view at each level

Option C: Prometheus + remote_write (Thanos / Mimir)
  + Prometheus handles short-term storage; object store handles long-term
  + Horizontally scalable query layer (Thanos Query)
  + High availability (Thanos Receive or Mimir ingest)
  − Significant operational complexity
  − Requires object storage (S3, GCS)
```

**This lab's choice:** Single Prometheus per phase (Option A). Thanos/Mimir is referenced in Phase 9 for multi-environment setups.

**Decision rule:** Start with a single Prometheus. Add remote_write when you hit 15-day retention limits or need cross-region federation.

---

### Decision 2 — Metric naming convention

**The question:** How do you name metrics so they are consistent, discoverable, and self-documenting?

The Prometheus naming convention:

```
{namespace}_{subsystem}_{name}_{unit}
```

| Part | Purpose | Example |
|---|---|---|
| `namespace` | The application or organisation | `lumio` |
| `subsystem` | The component within the application | `http`, `events`, `db` |
| `name` | What is being measured | `requests`, `duration`, `errors` |
| `unit` | The base unit (always use base units) | `seconds`, `bytes`, `total` |

Rules:
- Use `_total` suffix for counters (`requests_total`, not `requests_count`)
- Use `_seconds` for time, `_bytes` for size — never milliseconds or megabytes (use base SI units)
- Use snake_case, never camelCase or kebab-case
- Metric names must be unique within a process — use namespacing to avoid collisions

```python
# ✓ correct
lumio_http_requests_total
lumio_http_request_duration_seconds
lumio_events_processed_total

# ✗ avoid
http_req_count        # no namespace, ambiguous unit
LumioRequestDuration  # camelCase
lumio-events-total    # kebab-case — invalid in Prometheus
```

**This lab's choice:** All metrics prefixed with `lumio_`, using `_total` for counters and `_seconds` for latency. Established in Phase 0.

---

### Decision 3 — Log format and shipping strategy

**The question:** What format do your logs use, and how do they get from the application to Loki?

```
Option A: Unstructured logs → Promtail with regex parsing
  + No app changes required
  − Brittle: regex breaks when log format changes
  − Cannot query structured fields (customer_id, event_type)
  − High Loki ingestion cost (full text)

Option B: Structured JSON logs → Promtail with JSON parsing
  + Queryable by any field without regex
  + Consistent across all services
  + Low cost: Loki only indexes labels, stores raw JSON
  − Requires logging library change in the application
  − JSON is verbose (larger log volume)

Option C: OpenTelemetry Collector
  + Vendor-neutral: same collector ships to Loki, Jaeger, Tempo
  + Handles metrics, logs, and traces from one agent
  − More complex to configure and operate
  − Heavier resource footprint than Promtail
```

**This lab's choice:** Option B — structured JSON logs from Phase 4 onwards. Promtail with JSON parsing. This is the correct production choice for any service built after 2020.

---

### Decision 4 — Alerting strategy

**The question:** When should a metric fire an alert, and at what threshold?

```
Anti-pattern: Alert on every metric above a threshold
  cpu > 80% → page
  memory > 70% → page
  disk > 60% → page
  ✗ Fires constantly. Trains oncall to ignore alerts. Leads to alert fatigue.

Correct pattern: Alert on SLO burn rate
  checkout error budget burning > 5% per hour → warn
  checkout error budget burning > 14% per hour → page (critical)
  ✓ Only fires when user impact is real and material.
  ✓ Severity is calibrated to how fast the 28-day budget is being consumed.
  ✓ Suppresses noise from brief transient spikes.
```

The burn rate model: if your error budget for the month is 40 minutes (99.9% SLO), a burn rate of 1× means you will exhaust it in exactly 28 days. A burn rate of 14× means you will exhaust it in 2 days — that deserves a page.

**This lab's choice:** Symptom-based alerting on error rate and latency in Phase 2. SLO burn rate alerting in Phase 3.

---

### Decision 5 — Cardinality budget

**The question:** How many active time series can your Prometheus handle, and how do you stay within that budget?

A rule of thumb: 1 million active time series requires ~3GB of RAM and ~10GB of storage per 15 days (with default block compression). A t3.medium (4GB RAM) can handle roughly 500,000 time series comfortably.

Before adding a label, calculate its cardinality impact:

```
Current series count: 500
+ Adding label "environment" (3 values: dev/staging/prod) → 1,500 series
+ Adding label "region" (5 values) → 7,500 series
+ Adding label "customer_segment" (10 values) → 75,000 series

Adding label "customer_id" (10,000 customers) → 5,000,000 series  ← OOM
```

**This lab's choice:** Labels chosen in Phase 0 are deliberately low-cardinality. Phase 8 introduces cardinality monitoring with `prometheus_tsdb_head_series` and `topk(10, count by (__name__)({__name__=~".+"}))`.

---

## How this lab implements each principle

| Principle | Phase(s) |
|---|---|
| Instrument at the source | 0, 3 |
| Structure your data | 0, 4 |
| Alert on symptoms, not causes | 2, 3 |
| Define SLIs and SLOs before dashboards | 3 |
| Cardinality is a resource | 0, 8 |
| Dashboards are for humans | 1 |
| Correlate across pillars | 5 |

| Architecture decision | Phase(s) |
|---|---|
| Prometheus deployment model | 0, 9 |
| Metric naming convention | 0 |
| Log format and shipping | 4 |
| Alerting strategy | 2, 3 |
| Cardinality budget | 0, 8 |
| Retention strategy | 0, 9 |

---

## Reading order

You do not need to have all of this resolved before starting Phase 0. Phase 0 is intentionally simple — one service, one Prometheus, no alerting, no SLOs.

But you should have read this document before Phase 2 (alerting) — because the decisions you make about SLOs, alert routing, and label taxonomy in Phase 2 are difficult to change retroactively without rebuilding your dashboards and alert rules from scratch.

The principles exist not to slow you down at the start, but to prevent you from building something you will tear down in six months.

---

[Start: Phase 0 — First Metrics with Prometheus →](./phase-0-first-metrics/README.md)
