# Phase 9 — Multi-Environment Observability

> **Lumio, 80 engineers.**
> The on-call rotation now has 12 engineers.  Every Monday morning there's a new post-mortem comment: *"I got paged at 2am for LumioHighErrorRate but it was dev.  I spent 20 minutes investigating before realising nobody was using dev at that hour — a developer had left a bad config deployed from Friday."*  Engineers are starting to mute alerts.  Alert fatigue is setting in.  The solution isn't fewer alerts — it's smarter routing.

## What you will build

Two instances of the Lumio API — `api-dev` and `api-prod` — scraped by a single Prometheus.  The `env` label flows from the scrape target through every metric, every recording rule, and every alert.  Alertmanager routes alerts based on `env`:

| Route | Receiver | group_wait | repeat_interval |
|---|---|---|---|
| `env=dev` | webhook-dev | 60s | 24h |
| `env=prod, critical` | webhook-prod | 10s | 15m |
| `env=prod, warning` | webhook-prod | 30s | 4h |

You can watch both webhook logs simultaneously and see that a dev incident never touches the prod receiver.

---

## Concepts

### Where the env label comes from

Prometheus provides two mechanisms for adding environment labels.

**Option A — external_labels (multi-Prometheus setup)**

Each environment runs its own Prometheus instance with `external_labels` in `prometheus.yml`:

```yaml
# prod-prometheus.yml
global:
  external_labels:
    env: prod
    region: us-east-1
```

`external_labels` adds these labels to:
- Every metric scraped by this instance
- Every alert notification sent to Alertmanager
- Every payload sent via remote_write (to Thanos, Cortex, Mimir, etc.)

This is the pattern used in large organisations with dozens of clusters.

**Option B — static_configs labels (single-Prometheus setup)**

One Prometheus scrapes both environments.  The `env` label is added at the target level:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: lumio-api
    static_configs:
      - targets: ["api-dev:8000"]
        labels:
          env: dev
      - targets: ["api-prod:8000"]
        labels:
          env: prod
```

Labels defined in `static_configs.labels` are attached to every metric scraped from that target.  This is what this lab uses.

Both approaches produce the same result:
```
lumio_http_requests_total{job="lumio-api", env="dev",  instance="api-dev:8000",  ...}
lumio_http_requests_total{job="lumio-api", env="prod", instance="api-prod:8000", ...}
```

### Recording rules must preserve env in by()

A recording rule that doesn't include `env` in its `by()` clause **aggregates across all environments**:

```promql
# ❌ Wrong — sums dev + prod into a single series
sum(rate(lumio_http_requests_total[5m])) by (job)

# ✅ Correct — separate series per environment
sum(rate(lumio_http_requests_total[5m])) by (job, env)
```

The rule names in this phase reflect this: `job_env:lumio_http_requests_total:rate5m` (level includes `env`).

### Alert routing in Alertmanager

Alertmanager's route tree evaluates each incoming alert against a sequence of matchers.  The first matching route is used.  Routes can be nested.

```
route (catch-all → prod-receiver)
  ├── severity=none → null
  ├── env=dev → dev-receiver  (24h repeat)
  └── env=prod
       ├── severity=critical → prod-receiver  (15m repeat)
       └── severity=warning  → prod-receiver  (4h repeat)
```

The `group_by` field controls which alerts are batched into one notification.  Including `env` in `group_by` ensures dev and prod alerts are always in separate notification groups — they won't merge into one confusing message.

### Why group_wait differs by environment

`group_wait` is how long Alertmanager waits before sending the first notification after a new group opens.  A longer `group_wait` gives time for related alerts to arrive before sending — reducing notification count.

For dev:
- 60 seconds `group_wait` — dev alerts often self-heal (a developer fixes a bad deploy in seconds)
- 24h `repeat_interval` — once is enough; on-call doesn't need to be paged again for a dev issue

For prod critical:
- 10 seconds `group_wait` — page immediately, every minute counts
- 15 minutes `repeat_interval` — keep paging until someone acknowledges

### The inhibition rule and env

The inhibition rule from Phase 2 (suppress symptom alerts when LumioServiceDown) must specify `equal: [job, env]`:

```yaml
inhibit_rules:
  - source_matchers:
      - alertname = LumioServiceDown
    target_matchers:
      - alertname =~ "LumioHigh.*"
    equal: [job, env]   # ← critical: only inhibit within the same environment
```

Without `env` in `equal`, a dev service outage would suppress prod error-rate alerts — a dangerous false silence.

### The Grafana env template variable

The dashboard uses a Grafana template variable to filter panels by environment:

```json
{
  "type": "query",
  "name": "env",
  "query": "label_values(up{job=\"lumio-api\"}, env)"
}
```

This queries Prometheus for all distinct values of the `env` label on `up` metrics from the lumio-api job.  The variable populates automatically as environments are added — no dashboard changes needed when you add a `staging` environment.

Panels use `env=~"$env"` (regex match) to support multi-select (show All, or Dev+Prod simultaneously for comparison).

### The multi-Prometheus pattern vs single Prometheus

| | Single Prometheus + labels | Multi-Prometheus + external_labels |
|---|---|---|
| **Setup complexity** | Low | High (one Prometheus per env) |
| **Blast radius** | One Prometheus going down loses all environments | Per-env failure is isolated |
| **Cross-env queries** | Native (both envs in same TSDB) | Requires federation or Thanos |
| **Cardinality** | All series in one TSDB | Distributed across instances |
| **When to use** | Small teams, < 3 environments | Large orgs, many environments, strict isolation |

For teams beyond ~5 environments or with compliance requirements separating prod telemetry, the multi-Prometheus approach (one per env, all remote-writing to a central store like Thanos or Grafana Mimir) is standard.

---

## Stack

```
┌─────────────────────────────────────────────────────────────────┐
│  docker compose up                                              │
│                                                                 │
│  api-dev:8001        ← env=dev (higher start error rate)        │
│  api-prod:8000       ← env=prod                                 │
│  prometheus:9090     ← scrapes both with env label              │
│  alertmanager:9093   ← routes by env                            │
│  webhook-dev:5001    ← only receives dev alerts                 │
│  webhook-prod:5002   ← only receives prod alerts                │
│  grafana:3000        ← env template variable dashboard          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Challenges

### Challenge 1 — Start the stack and verify env labelling

```bash
cd phase-9-multienv/app
docker compose up --build -d
./load.sh
```

Verify that the `env` label is present on scraped metrics:

```bash
curl -sg 'http://localhost:9090/api/v1/query?query=up{job="lumio-api"}' \
  | python3 -m json.tool
```

You should see two results — one with `env="dev"` and one with `env="prod"`.

Then verify the recording rules are producing per-env series:

```bash
curl -sg 'http://localhost:9090/api/v1/query?query=job_env:lumio_http_requests_total:rate5m' \
  | python3 -m json.tool
```

Two series should be returned — one per environment.

Open the **Lumio API — Multi-Environment** dashboard: http://localhost:3000/d/lumio-multienv

Use the **Environment** variable at the top to switch between `All`, `dev`, and `prod`.

---

### Challenge 2 — Observe the routing in action (dev incident)

In two separate terminals:

```bash
# Terminal 1 — watch dev alerts
docker compose logs -f webhook-dev

# Terminal 2 — watch prod alerts (should stay silent)
docker compose logs -f webhook-prod
```

Now trigger a high error rate in dev:

```bash
./break.sh dev 0.5
```

After `group_wait` (60 seconds) you should see:
- **webhook-dev** receives `[dev] Lumio error rate above 5%`
- **webhook-prod** receives nothing

This is the core Phase 9 result: the same alert rule fires in dev, but it doesn't wake the prod on-call.

Reset:
```bash
./break.sh dev 0.0
```

---

### Challenge 3 — Observe the routing in action (prod incident)

```bash
./break.sh prod 0.5
```

After `group_wait` (10 seconds — much shorter than dev):
- **webhook-prod** receives `[prod] Lumio error rate above 5%` with severity `critical`
- **webhook-dev** receives nothing

Within 15 minutes (the `repeat_interval`) you'll receive a second notification from webhook-prod — it keeps paging.

Reset:
```bash
./break.sh prod 0.0
```

---

### Challenge 4 — Compare dev and prod on the same dashboard

Trigger both environments simultaneously:

```bash
./break.sh dev 0.5
./break.sh prod 0.3
```

Open the dashboard: http://localhost:3000/d/lumio-multienv

Set the **Environment** variable to `All`.  The **Error Rate — dev vs prod** panel shows both lines — you can see that prod is at 30% and dev is at 50%, but only the correct receivers get notified.

Note the **Alertmanager Notifications Sent** panel — it shows `dev-receiver` and `prod-receiver` as separate lines, making the routing observable within Grafana.

Also view the Alertmanager UI: http://localhost:9093

You'll see the firing alerts grouped by `[alertname, job, env]` — dev and prod groups are separate.

Reset both:
```bash
./break.sh dev 0.0
./break.sh prod 0.0
```

---

### Challenge 5 — Understand inhibition with env

Verify the inhibition rule protects cross-environment isolation.

Stop the dev API to trigger `LumioServiceDown` for dev:

```bash
docker compose stop api-dev
```

Wait 1 minute.  Check the Alertmanager: http://localhost:9093/alerts

You should see:
- `LumioServiceDown{env="dev"}` firing
- `LumioHighErrorRate{env="dev"}` **inhibited** (suppressed by the service-down alert)
- Prod alerts: **unaffected** — prod is still healthy

Restart dev:
```bash
docker compose start api-dev
```

Now look at `alertmanager.yml` and find the inhibition rule.  Note `equal: [job, env]`.

**What happens without `env` in equal?**

If you remove `env` from the `equal` list, a dev service outage would inhibit prod error-rate alerts — on-call wouldn't be paged even if prod was on fire.  The `equal` field is what makes inhibition environment-safe.

---

### Challenge 6 — Per-environment thresholds with Alertmanager routes

Currently the same threshold (5% error rate = critical) applies to both dev and prod.  In practice you often want:
- Dev: 20% error rate before firing (developers test error paths)
- Prod: 1% error rate is already a warning

One approach is to encode thresholds in the alert rule using `unless`:

```yaml
# Fire as critical only when env=prod and rate > 0.05
- alert: LumioHighErrorRateProd
  expr: >
    job_env:lumio_http_requests_error_ratio:rate5m{job="lumio-api", env="prod"} > 0.05
  for: 2m
  labels:
    severity: critical

# Fire as warning only when env=dev and rate > 0.20
- alert: LumioHighErrorRateDev
  expr: >
    job_env:lumio_http_requests_error_ratio:rate5m{job="lumio-api", env="dev"} > 0.20
  for: 5m
  labels:
    severity: warning
```

This works but creates rule duplication.  An alternative is to keep a single alert rule and use Alertmanager routing with `active_time_intervals` to suppress dev alerts during off-hours:

```yaml
# alertmanager.yml
time_intervals:
  - name: business-hours
    time_intervals:
      - weekdays: [monday:friday]
        times:
          - start_time: "09:00"
            end_time:   "18:00"

routes:
  - matchers:
      - env = dev
    receiver: dev-receiver
    active_time_intervals: [business-hours]   # dev alerts only during business hours
```

**Try it:** Add a `time_intervals` block to `alertmanager/alertmanager.yml` and reload:
```bash
curl -X POST http://localhost:9093/-/reload
```

---

### Challenge 7 — Add a staging environment

The single-Prometheus approach makes adding a third environment trivial.

In `docker-compose.yml`, add:
```yaml
api-staging:
  build: ./api
  ports:
    - "8002:8000"
  environment:
    - ERROR_RATE=0.02
```

In `prometheus/prometheus.yml`, add a target:
```yaml
- targets: ["api-staging:8000"]
  labels:
    env: staging
```

Reload Prometheus:
```bash
curl -X POST http://localhost:9090/-/reload
```

Open the dashboard — the **Environment** variable will automatically show `staging` as a new option (populated via `label_values(up{job="lumio-api"}, env)`).

In `alertmanager/alertmanager.yml`, add a staging route between dev and prod:
```yaml
- matchers:
    - env = staging
  receiver: dev-receiver     # staging alerts go to the same receiver as dev
  group_wait:      30s
  repeat_interval: 8h
```

No dashboard changes needed — the template variable discovers the new environment automatically.

---

### Challenge 8 — Understand external_labels for production architectures

Open `prometheus/prometheus.yml` and read the comment about `external_labels`.

In a production multi-cluster setup you would run a separate Prometheus per environment:

```
prod-prometheus.yml             dev-prometheus.yml
  global:                         global:
    external_labels:                external_labels:
      env: prod                       env: dev
      region: us-east-1              region: local

  scrape_configs:                 scrape_configs:
    - job_name: lumio-api           - job_name: lumio-api
      ...                             ...
```

Both Prometheus instances remote-write to a central store (Thanos Receive, Grafana Mimir, or Prometheus remote_write to a federation Prometheus):

```yaml
remote_write:
  - url: "http://thanos-receive:19291/api/v1/receive"
```

The central store receives metrics from all instances.  Each metric carries the `env` label from `external_labels`, making them queryable across all environments in one Grafana dashboard.

**Key advantages of multi-Prometheus over the single-Prometheus + labels approach:**

1. **Failure isolation**: a dev Prometheus OOMing doesn't affect prod visibility
2. **Scrape proximity**: the Prometheus instance is co-located with its targets (same VPC/cluster), minimising network hops
3. **Independent retention**: prod can keep 1 year of data; dev can keep 7 days
4. **Cardinality isolation**: a cardinality explosion in dev only affects the dev Prometheus

---

## File structure

```
phase-9-multienv/
└── app/
    ├── docker-compose.yml          ← api-dev (8001), api-prod (8000), two webhooks
    ├── load.sh                     ← ./load.sh [dev|prod|both]
    ├── break.sh                    ← ./break.sh <dev|prod> <rate>
    ├── api/
    │   └── app.py                  ← fixed version from Phase 8 (no cardinality bug)
    ├── alertmanager/
    │   └── alertmanager.yml        ← routes by env; inhibition with equal: [job, env]
    ├── webhook/
    │   └── webhook.py              ← supports RECEIVER_NAME env var; colour-coded by env
    ├── prometheus/
    │   ├── prometheus.yml          ← static_configs with env labels per target
    │   └── rules/
    │       ├── lumio_recording.yml ← recording rules with env in all by() clauses
    │       └── lumio_alerting.yml  ← [{{ $labels.env }}] prefix in all summaries
    └── grafana/
        └── dashboards/
            └── lumio-multienv.json ← env template variable; dev vs prod error rate panel
```

---

## Command reference

```bash
# Start
docker compose up --build -d

# Load both environments
./load.sh

# Load a specific environment
./load.sh dev
./load.sh prod

# Trigger errors in a specific environment
./break.sh dev  0.5
./break.sh prod 0.5

# Reset
./break.sh dev  0.0
./break.sh prod 0.0

# Watch alert routing in real time (run in separate terminals)
docker compose logs -f webhook-dev
docker compose logs -f webhook-prod

# Verify env label on scraped metrics
curl -sg 'http://localhost:9090/api/v1/query?query=up{job="lumio-api"}' \
  | python3 -m json.tool

# Check recording rule output per env
curl -sg 'http://localhost:9090/api/v1/query?query=job_env:lumio_http_requests_error_ratio:rate5m' \
  | python3 -m json.tool

# Reload Alertmanager after config change
curl -X POST http://localhost:9093/-/reload

# Reload Prometheus after config change
curl -X POST http://localhost:9090/-/reload

# Add staging environment (edit docker-compose.yml and prometheus.yml first)
docker compose up -d api-staging
curl -X POST http://localhost:9090/-/reload

# Stop
docker compose down -v
```

---

## What this doesn't do yet

| Gap | Next phase |
|---|---|
| Everything is self-managed — who owns the infrastructure? | Phase 10 — Capstone |
| No SLOs defined; no error budget tracking | Phase 10 — production platform |

---

## Production considerations

**Add `env` on day one.** It is trivially cheap to add an `env` label.  Retrofitting it once you have dozens of dashboards and hundreds of alert rules is painful.

**Use `external_labels` when Prometheus is colocated with its targets.** The single-Prometheus approach works up to ~3–5 environments.  Beyond that, or when environments require network isolation, run a Prometheus per cluster and remote-write to a central store.

**Never let dev and prod share an Alertmanager route without an env filter.** The most common Phase 9 mistake is a catch-all route at the top of the route tree that sends everything to prod-receiver.  Always put env-specific routes before the catch-all.

**Include env in every group_by.** Omitting `env` from `group_by` causes dev and prod alerts for the same alertname to be batched into one notification — confusing, and potentially causing a prod alert to be delayed because it was grouped with a dev alert that hadn't yet fired.

**Verify inhibition rules include `equal: [env]`.** A dev service outage should never suppress prod alerts.  Test this explicitly: stop a dev container, verify prod alerts still fire, then check the Alertmanager inhibited list to confirm only dev alerts are inhibited.
