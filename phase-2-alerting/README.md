# Phase 2 — Alerting with Alertmanager

> **Concepts introduced:** Alerting rule, pending/firing state, `for` duration, alert labels vs annotations, Alertmanager route tree, receiver, grouping, inhibition rules, dead man's switch, alert fatigue, symptom-based alerting

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Alerting rule** | A PromQL expression evaluated on a schedule; fires when true | The bridge between a metric anomaly and a notification |
| **Pending state** | Alert condition is true but hasn't held for the `for` duration yet | Prevents flapping — a 1-second spike should not page anyone |
| **Firing state** | Alert has been pending for the full `for` duration | The alert is now real and routed to Alertmanager |
| **`for` duration** | How long the condition must hold before the alert fires | Short (1m) for critical symptoms; longer (5m+) for soft warnings |
| **Alert labels** | Key-value pairs that are part of the alert's identity | Used for routing (`severity`, `team`); grouping; inhibition matching |
| **Alert annotations** | Human-readable metadata on the alert | `summary` and `description` appear in the notification; not used for routing |
| **Alertmanager** | Receives alerts from Prometheus; deduplicates, groups, routes, and silences | A single Prometheus can only evaluate rules; Alertmanager handles the operational complexity |
| **Route tree** | Nested matching rules in `alertmanager.yml`; first match wins | Directs alerts to different receivers based on severity, team, or any label |
| **Receiver** | A notification channel: webhook, email, Slack, PagerDuty | Where the human gets notified |
| **Grouping** | Bundling multiple alerts into one notification | Prevents 50 individual pages when one incident causes 50 alerts |
| **Inhibition rule** | Suppresses target alerts while a source alert is firing | Stops `LumioHighErrorRate` from paging when `LumioServiceDown` is already firing |
| **Dead man's switch** | An alert that always fires; its absence means the pipeline is broken | Detects failures in Prometheus or Alertmanager itself |
| **Alert fatigue** | Engineers tuning out alerts because too many are noise | The single most common cause of missed incidents in teams with immature alerting |

---

## The problem

> *Lumio — 15 engineers. Three weeks after Phase 1.*
>
> The dashboards were working. When someone looked at them, they knew in 30 seconds whether the service was healthy. The problem was: nobody was looking.
>
> The team found out about incidents from customers. A checkout event had been failing for two hours. The error rate panel was red the entire time — but nobody had Grafana open on a Friday afternoon.
>
> "We need to be told when something is wrong, not discover it."
>
> One engineer spent an afternoon writing alerting rules and wiring Alertmanager. From that point, the oncall engineer was paged within 5 minutes of any significant degradation. The two-hour silent incident became a 4-minute response time.

---

## Architecture

```
phase-2-alerting/app/

  ┌─────────────┐   /metrics    ┌────────────────┐  fires alert  ┌─────────────────┐
  │  lumio-api  │◄──────────── │   Prometheus   │──────────────►│  Alertmanager   │
  │  :8000      │  every 15s   │   :9090        │               │  :9093          │
  └─────────────┘              │                │               └────────┬────────┘
                               │  evaluates     │                        │ routes
                               │  rules/ every  │                        ▼
                               │  15s           │               ┌─────────────────┐
                               └────────────────┘               │  webhook :5001  │
                                                                 │  (logs to       │
                                       ┌────────────────┐        │  stdout)        │
                                       │  Grafana :3000 │        └─────────────────┘
                                       │  (dashboards)  │
                                       └────────────────┘
```

---

## Repository structure

```
phase-2-alerting/
└── app/
    ├── docker-compose.yml
    ├── load.sh
    ├── break.sh                         ← triggers an incident
    ├── api/
    │   ├── app.py                       ← adds /admin/set-error-rate endpoint
    │   ├── Dockerfile
    │   └── requirements.txt
    ├── prometheus/
    │   ├── prometheus.yml               ← points at Alertmanager + rule_files
    │   └── rules/
    │       └── lumio.yml                ← alerting rules
    ├── alertmanager/
    │   └── alertmanager.yml             ← route tree + receivers + inhibit_rules
    ├── webhook/
    │   ├── webhook.py                   ← logs received alerts to stdout
    │   ├── Dockerfile
    │   └── requirements.txt
    └── grafana/
        ├── provisioning/
        └── dashboards/
```

---

## Challenge 1 — Start the stack and meet Alertmanager

### Step 1: Start the stack

```bash
cd phase-2-alerting/app
docker compose up -d --build
```

Five containers start: `api`, `prometheus`, `alertmanager`, `webhook`, `grafana`.

### Step 2: Open the Alertmanager UI

Open **http://localhost:9093**.

Navigate to **Alerts** — you should see `LumioWatchdog` already firing. This is the dead man's switch: an alert that always fires, confirming the pipeline is alive. (We route it to a null receiver — it never notifies anyone, but its absence would be the signal something is wrong.)

### Step 3: Check Prometheus rule evaluation

Open **http://localhost:9090/alerts**.

You will see all configured alerting rules and their current state:
- `LumioWatchdog` — **Firing** (always)
- `LumioServiceDown` — **Inactive** (service is up)
- `LumioHighErrorRate` — **Inactive** (error rate is ~2%)
- `LumioElevatedErrorRate` — **Inactive**
- `LumioHighP95Latency` — **Inactive**

---

## Challenge 2 — Understand alerting rules

Open `prometheus/rules/lumio.yml`.

### The rule structure

```yaml
- alert: LumioHighErrorRate
  expr: |
    (
      sum(rate(lumio_http_requests_total{status_code=~"5.."}[5m]))
      /
      sum(rate(lumio_http_requests_total[5m]))
    ) > 0.05
  for: 2m
  labels:
    severity: critical
    team: platform
  annotations:
    summary: "High HTTP error rate on Lumio API"
    description: "Error rate is {{ $value | humanizePercentage }}, above the 5% threshold."
```

**`expr`** — any valid PromQL expression. When it returns a non-empty result set, the alert is active. The expression value (`$value`) is available in annotations via Go templating.

**`for: 2m`** — the condition must hold for 2 minutes before the alert transitions from **pending** to **firing** and is sent to Alertmanager. Set this short (1–2m) for critical symptoms, longer (5–10m) for noisy metrics that spike briefly.

**`labels`** — added to the alert's label set. These are used by Alertmanager's route tree for matching. `severity` determines which route fires and how urgently.

**`annotations`** — human-readable metadata. `summary` is a one-line description. `description` has detail. `runbook` can link to a wiki page. These appear in notifications but do not affect routing.

### Labels vs annotations — the critical distinction

| | Labels | Annotations |
|---|---|---|
| Purpose | Identity and routing | Human display |
| Affect routing | Yes | No |
| Affect grouping | Yes | No |
| Appear in alerts URL | Yes | No |
| Example | `severity: critical` | `summary: "Error rate > 5%"` |

A common mistake is putting routing information in annotations. If you add `team: platform` as an annotation instead of a label, the Alertmanager route that matches on `team = "platform"` will never fire for that alert.

---

## Challenge 3 — Trigger an incident

### Step 1: Start load

```bash
chmod +x load.sh && ./load.sh
```

### Step 2: Watch webhook output in another terminal

```bash
docker compose logs -f webhook
```

You will see nothing — the baseline error rate of ~2% is below the warning threshold.

### Step 3: Break it

```bash
chmod +x break.sh && ./break.sh
```

This calls `POST /admin/set-error-rate {"rate": 0.5}` — setting the simulated error rate to 50%.

### Step 4: Watch the state machine

In **http://localhost:9090/alerts**, watch `LumioHighErrorRate` and `LumioElevatedErrorRate`:

```
Inactive → Pending (condition became true)
Pending  → Firing  (condition held for the `for` duration — 2 minutes)
```

Once firing, the webhook terminal shows:

```
[FIRING] LumioHighErrorRate (critical)
  Summary: High HTTP error rate on Lumio API
  Description: Error rate is 50%, above the 5% critical threshold.
  Started: 2025-01-15T14:23:01Z
```

### Step 5: Restore and watch resolution

```bash
./break.sh 0.02   # restore to 2%
```

Within 5 minutes you will see:

```
[RESOLVED] LumioHighErrorRate (critical)
  ...
  Resolved: 2025-01-15T14:28:45Z
```

`send_resolved: true` in the Alertmanager config is what enables resolved notifications.

---

## Challenge 4 — Understand the route tree

Open `alertmanager/alertmanager.yml`.

```yaml
route:
  group_by: ["alertname", "team"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 1h
  receiver: default

  routes:
    - matchers:
        - alertname = "LumioWatchdog"
      receiver: "null"

    - matchers:
        - severity = "critical"
      receiver: webhook
      group_wait: 10s
      repeat_interval: 15m

    - matchers:
        - severity = "warning"
      receiver: webhook
      repeat_interval: 30m
```

### How routing works

Alertmanager walks the route tree top-to-bottom. For each incoming alert, it checks child routes in order. The first match wins. If no child matches, the root route handles it.

For `LumioHighErrorRate` (labels: `severity=critical, team=platform`):
1. Does `alertname = "LumioWatchdog"` match? No.
2. Does `severity = "critical"` match? **Yes** → route to `webhook` receiver with `group_wait: 10s`.

For `LumioWatchdog` (labels: `severity=none`):
1. Does `alertname = "LumioWatchdog"` match? **Yes** → route to `null` receiver (suppressed).

### Grouping

`group_by: ["alertname", "team"]` means alerts with the same `alertname` and `team` are bundled into one notification.

`group_wait: 30s` — when a new group arrives, wait 30 seconds for more alerts with the same grouping to arrive before sending the first notification. This prevents three individual pages when three alerts fire within 10 seconds of each other.

`group_interval: 5m` — if new alerts arrive in an existing group, wait 5 minutes before sending a new notification.

`repeat_interval: 1h` — if the alert is still firing after 1 hour, re-notify. Critical alerts have `repeat_interval: 15m` — shorter, because critical means customers are being affected.

---

## Challenge 5 — Inhibition rules

Open the `inhibit_rules` section in `alertmanager.yml`:

```yaml
inhibit_rules:
  - source_matchers:
      - alertname = "LumioServiceDown"
    target_matchers:
      - alertname =~ "LumioHigh.*|LumioElevated.*"
    equal: ["team"]
```

### Test it

Simulate the service going completely down by stopping the API container:

```bash
docker compose stop api
```

After ~1 minute, `LumioServiceDown` fires. Within ~2 minutes, `LumioHighErrorRate` and others would also fire — but the inhibition rule suppresses them. Only `LumioServiceDown` appears in the webhook output.

Why? When the service is down, the error rate alert is not providing new information — it is a consequence of the service being down, not a separate problem. The oncall should focus on bringing the service back, not triaging three different notifications about the same root cause.

Restore:

```bash
docker compose start api
```

The inhibition lifts as soon as `LumioServiceDown` resolves.

---

## Challenge 6 — The dead man's switch

`LumioWatchdog` uses `expr: vector(1)` — a PromQL expression that always returns a result (the scalar `1`). This alert always fires.

### Why it matters

Consider what happens if Prometheus itself crashes. Your alerting rules are not evaluated. Alerts that should fire do not fire. Your oncall receives no notification. The service could be completely down and no one would know — because the alerting pipeline itself is broken.

The dead man's switch inverts this:

1. Configure your on-call tool (PagerDuty, OpsGenie, Grafana OnCall) to expect a heartbeat from `LumioWatchdog` every N minutes.
2. If the heartbeat stops arriving, page the oncall.

The absence of a signal becomes the signal.

In this lab, `LumioWatchdog` routes to the `null` receiver (suppressed). In production, it would route to a dedicated "watchdog" receiver in PagerDuty or OpsGenie configured as a heartbeat monitor.

---

## Challenge 7 — Add a Slack receiver (optional)

To connect a real Slack workspace:

### Step 1: Create an incoming webhook in Slack

In your Slack workspace: **Apps → Incoming Webhooks → Add to Slack** → choose a channel → copy the webhook URL.

### Step 2: Add the receiver to alertmanager.yml

```yaml
receivers:
  - name: slack
    slack_configs:
      - api_url: "https://hooks.slack.com/services/T.../B.../..."
        channel: "#alerts"
        title: "{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}"
        text: >-
          {{ range .Alerts }}
          *Status:* {{ .Status }}
          *Alert:* {{ .Labels.alertname }}
          *Severity:* {{ .Labels.severity }}
          *Description:* {{ .Annotations.description }}
          {{ end }}
        send_resolved: true
```

Update the `webhook` route to use `slack` as the receiver. Reload Alertmanager:

```bash
curl -X POST http://localhost:9093/-/reload
```

---

## Challenge 8 — Alert on symptoms, not causes

Open `prometheus/rules/lumio.yml` and look at what we alert on vs what we do not.

**We alert on:**
- `LumioHighErrorRate` — customers are receiving errors (symptom)
- `LumioHighP95Latency` — customers are experiencing slowness (symptom)
- `LumioServiceDown` — service is unreachable (symptom)

**We do not alert on:**
- CPU usage
- Memory usage
- Goroutine count
- GC pause duration

This is intentional. CPU at 90% is not a problem if customers are happy. It becomes a problem if it is causing latency or errors — in which case the latency or error alert fires. Alerting on causes (CPU, memory) leads to alert fatigue: the oncall gets paged at 3am for a CPU spike that resolved itself and nobody noticed any impact.

The rule: **alert on the user-visible symptom, not the internal cause.** Use dashboards for causes; use alerts for symptoms.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start the full stack |
| `./load.sh` | Generate steady traffic |
| `./break.sh` | Set error rate to 50% (triggers critical alert) |
| `./break.sh 0.02` | Restore normal error rate |
| `docker compose logs -f webhook` | Watch alert notifications in real time |
| `docker compose stop api` | Simulate service-down (triggers inhibition test) |
| `docker compose start api` | Restore service |
| `curl -X POST http://localhost:9093/-/reload` | Reload Alertmanager config without restart |
| `curl -X POST http://localhost:9090/-/reload` | Reload Prometheus config and rules without restart |

| UI | URL |
|---|---|
| Alertmanager | http://localhost:9093 |
| Prometheus alerts | http://localhost:9090/alerts |
| Prometheus rules | http://localhost:9090/rules |
| Grafana | http://localhost:3000 (admin / lumio) |

---

## What this doesn't do yet

| Issue | Impact | Fixed in |
|---|---|---|
| No logs | When alerts fire, root cause requires SSHing into containers | Phase 4 |
| Alerts have no trace context | You know an endpoint is slow but not which code path | Phase 3 |
| Alert thresholds are static | Normal traffic growth causes false positives | Phase 11 (Davis AI) |
| No multi-window burn rate alerting | Error budget alerts not yet implemented | Phase 9 |

---

## Production considerations

### 1. Keep `for` durations short for symptoms, longer for saturation
`LumioServiceDown` uses `for: 1m` — if the service is down, you want to know quickly. `LumioHighP95Latency` uses `for: 5m` — latency is noisier, brief spikes are common. A P95 spike that lasts 4 minutes and 59 seconds never pages; one that lasts 5 minutes and 1 second does. This asymmetry is intentional.

### 2. Never alert on a raw counter
`lumio_http_requests_total > 1000` is not a valid alert. Counters only go up. Use `rate()` to get a per-second rate, then threshold that.

### 3. Always set `send_resolved: true` on production receivers
Without resolved notifications, oncall engineers do not know when an incident is over — they have to poll the dashboard. Resolved notifications close the incident loop automatically.

### 4. Use runbook links in annotations
```yaml
annotations:
  runbook: "https://wiki.lumio.io/runbooks/high-error-rate"
```
At 3am during an incident, the oncall should not have to remember what to do. A runbook link in every alert notification reduces MTTR significantly.

### 5. Test your alerting pipeline in staging
Use a staging environment that generates synthetic load and synthetic failures. Trigger every alert rule at least monthly. An alerting pipeline that has never been tested end-to-end has unknown reliability.

---

## Outcome

The Lumio team now gets paged within 5 minutes of any significant degradation — not when a customer calls. The alerting pipeline covers the three most important symptom categories: availability (`LumioServiceDown`), errors (`LumioHighErrorRate`), and latency (`LumioHighP95Latency`). The dead man's switch ensures the pipeline itself is monitored.

The oncall is not yet overwhelmed with noise, but alert fatigue is manageable — three rules, two severity levels, one receiver, clear inhibition logic.

Nobody has to stare at a Grafana dashboard to know the service is on fire.

---

[← Back to Phase 1 — Grafana Dashboards](../phase-1-grafana/README.md) | [Next: Phase 3 — OpenTelemetry →](../phase-3-opentelemetry/README.md)
