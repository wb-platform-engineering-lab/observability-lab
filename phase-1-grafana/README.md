# Phase 1 — Grafana Dashboards

> **Concepts introduced:** Datasource, Panel, Dashboard, Time series panel, Stat panel, Gauge panel, Threshold, Template variable, Dashboard provisioning, Dashboard as code

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Datasource** | A connection from Grafana to a data backend (Prometheus, Loki, etc.) | Grafana does not store data — it queries it; the datasource is the bridge |
| **Panel** | A single visualisation inside a dashboard | The unit of composition — each panel has one job |
| **Dashboard** | A collection of panels with a shared time range and variables | The operational artefact: what the oncall looks at during an incident |
| **Time series panel** | A line/bar chart over time | The primary panel type for rate, latency, and saturation metrics |
| **Stat panel** | A single large number with optional colour coding | Instant-read values: current error rate, active requests, uptime |
| **Gauge panel** | A dial showing a value between min and max | Saturation metrics where the scale matters: queue fill %, active connections |
| **Threshold** | A value boundary that changes a panel's colour | Makes dashboards self-explanatory — green means OK, red means investigate |
| **Template variable** | A dashboard-level dropdown that parameterises queries | One dashboard for all endpoints / services / environments instead of N copies |
| **Dashboard provisioning** | Grafana loads dashboards from JSON files at startup | Dashboards as code — version-controlled, reproducible, no manual UI steps |
| **Dashboard as code** | Dashboard stored as JSON in git | Review changes in PRs, deploy via CI, roll back with `git revert` |

---

## The problem

> *Lumio — 12 engineers. Two weeks after Phase 0.*
>
> The metrics were there. Prometheus was scraping every 15 seconds. `lumio_http_requests_total` was counting. `lumio_http_request_duration_seconds_bucket` had 60 data points per endpoint per minute. The data was perfect.
>
> Nobody was looking at it.
>
> Querying raw PromQL in the Prometheus UI requires knowing the metric names, remembering the label structure, and typing `histogram_quantile(0.95, sum by(le)(rate(...)))` correctly at 3am during an incident. That is not an incident response procedure. That is archaeology.
>
> The team needed dashboards. Something that loaded in one click, showed the right things without typing, and could be shared with the on-call rota, the CTO, and the customer success team who kept asking "is the API healthy right now?"
>
> One engineer spent an afternoon building the first Grafana dashboard. By 6pm they had six panels: requests per second, error rate, P50/P95/P99 latency, active requests, events by type, and status code breakdown. The next incident was diagnosed in 8 minutes.

---

## Architecture

```
phase-1-grafana/app/

  ┌─────────────┐    GET /metrics     ┌────────────────┐    PromQL     ┌─────────────┐
  │  lumio-api  │◄───────────────────│   Prometheus   │◄─────────────│   Grafana   │
  │  :8000      │   every 15 seconds  │   :9090        │              │   :3000     │
  └─────────────┘                    └────────────────┘              └─────────────┘
                                                                            │
                                                               ┌────────────┴────────────┐
                                                               │  Dashboards (from JSON) │
                                                               │  - Lumio API Overview   │
                                                               └─────────────────────────┘
```

---

## Repository structure

```
phase-1-grafana/
└── app/
    ├── docker-compose.yml
    ├── load.sh
    ├── api/                              ← same as Phase 0
    │   ├── Dockerfile
    │   ├── app.py
    │   └── requirements.txt
    ├── prometheus/
    │   └── prometheus.yml                ← same as Phase 0
    └── grafana/
        ├── provisioning/
        │   ├── datasources/
        │   │   └── prometheus.yml        ← auto-wires Prometheus as a datasource
        │   └── dashboards/
        │       └── lumio.yml             ← tells Grafana where to load dashboard JSON
        └── dashboards/
            └── lumio-overview.json       ← pre-built dashboard (study or import)
```

---

## Challenge 1 — Start the stack and open the pre-built dashboard

### Step 1: Start the stack

```bash
cd phase-1-grafana/app
docker compose up -d --build
```

This starts three containers: `api`, `prometheus`, and `grafana`. Wait ~10 seconds for Grafana to initialise.

```bash
docker compose ps
```

All three should be `Up`.

### Step 2: Log in to Grafana

Open **http://localhost:3000** in a browser.

```
Username: admin
Password: lumio
```

### Step 3: Find the pre-provisioned dashboard

Navigate to **Dashboards → Browse**. You will see a folder called **Lumio** containing **Lumio API Overview**.

Click it. The dashboard is empty (no data yet — Prometheus has just started).

### Step 4: Generate load and watch the dashboard update

```bash
chmod +x load.sh && ./load.sh
```

Leave it running. The dashboard refreshes every 30 seconds. Within a minute you will see:

- **Requests per second:** climbing to ~8–10 RPS
- **Error rate:** hovering around 2% (simulated)
- **Request latency:** P95 around 50–100ms, P99 higher and more variable
- **Events processed by type:** a stacked area chart of all five event types

---

## Challenge 2 — Understand how provisioning works

The dashboard and datasource appeared automatically at startup — no manual UI clicks. This is Grafana provisioning.

### Step 1: The datasource provisioning file

```bash
cat phase-1-grafana/app/grafana/provisioning/datasources/prometheus.yml
```

```yaml
datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus          # ← deterministic UID — used in dashboard JSON
    url: http://prometheus:9090
    isDefault: true
    editable: false          # ← prevents accidental UI changes
```

Grafana reads every file in `/etc/grafana/provisioning/datasources/` at startup and creates the datasources. The `uid: prometheus` is critical — it must match the `uid` referenced in every dashboard panel's datasource block. Without a deterministic UID, dashboard JSON is not portable.

### Step 2: The dashboard provisioning file

```bash
cat phase-1-grafana/app/grafana/provisioning/dashboards/lumio.yml
```

```yaml
providers:
  - name: Lumio
    folder: Lumio
    type: file
    disableDeletion: true      # ← dashboard cannot be deleted from the UI
    allowUiUpdates: false      # ← UI edits are discarded on next reload
    options:
      path: /var/lib/grafana/dashboards
```

This tells Grafana: *"scan `/var/lib/grafana/dashboards` for JSON files and load them as dashboards in the Lumio folder."*

The `allowUiUpdates: false` setting is intentional. If you want to change a dashboard, you change the JSON file and commit the change — not edit it in the UI and hope someone exports it later.

### Step 3: The dashboard JSON structure

```bash
cat phase-1-grafana/app/grafana/dashboards/lumio-overview.json | python3 -m json.tool | head -40
```

Key fields:
```json
{
  "uid": "lumio-overview",      // Stable ID — used in URLs and links
  "title": "Lumio API Overview",
  "refresh": "30s",             // Auto-refresh interval
  "panels": [...]               // Array of panel objects
}
```

Each panel object contains:
- `type` — visualisation type (`timeseries`, `stat`, `gauge`)
- `targets` — array of PromQL queries
- `fieldConfig` — units, thresholds, display options
- `gridPos` — position and size on the dashboard grid

---

## Challenge 3 — Build a panel from scratch: request rate

This challenge walks you through building a panel manually so you understand what the provisioned dashboard is doing.

### Step 1: Create a new dashboard

Click the **+** icon in the left sidebar → **New dashboard** → **Add visualization**.

### Step 2: Enter the query

In the query editor at the bottom, select **Prometheus** as the datasource (it should already be selected).

Switch to **Code** mode (toggle in the top-right of the query editor).

Enter:

```
sum(rate(lumio_http_requests_total[5m])) by (endpoint)
```

### Step 3: Configure the legend

In the **Legend** field below the query, enter:

```
{{endpoint}}
```

This uses the `endpoint` label value as the series name. Without this, Grafana uses the full metric name + labels as the legend — unreadable.

### Step 4: Configure the panel

- Set the **Panel title** (top-right of the panel editor): `Requests per second`
- In **Field** → **Unit**, search for `requests/sec` (or type `reqps`)
- In **Graph styles** → **Fill opacity**, set to `10`
- In **Graph styles** → **Stacking**, set to `Normal`

Click **Apply**. You now have a stacked area chart showing RPS broken down by endpoint.

---

## Challenge 4 — Build the error rate stat panel

A stat panel shows a single large number — ideal for metrics that need an at-a-glance reading.

### Step 1: Add a new panel

Click **Add** → **Visualization**.

### Step 2: Enter the query

```
100 * sum(rate(lumio_http_requests_total{status_code=~"5.."}[5m]))
      /
      sum(rate(lumio_http_requests_total[5m]))
```

### Step 3: Set the visualisation type

In the top-right panel type selector, choose **Stat**.

### Step 4: Configure units and thresholds

Under **Field**:
- **Unit:** `Percent (0-100)`
- **Decimals:** `2`

Under **Thresholds** (click **+ Add threshold**):
- Default (no threshold): green
- Value `1`: yellow
- Value `5`: red

Under **Standard options** → **Color scheme**: choose `Thresholds`.

Under **Stat styles** → **Color mode**: choose `Background` — the entire panel background changes colour.

Click **Apply**. The panel shows the current error rate as a number with a coloured background: green when healthy, yellow when degraded, red when on fire.

---

## Challenge 5 — Build the P95 latency time series

### Step 1: Add a new panel with three queries

Add a new visualization and enter three queries (click **+ Add query** to add the second and third):

**Query A** — legend `P50`:
```
histogram_quantile(0.50, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
```

**Query B** — legend `P95`:
```
histogram_quantile(0.95, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
```

**Query C** — legend `P99`:
```
histogram_quantile(0.99, sum by(le)(rate(lumio_http_request_duration_seconds_bucket[5m])))
```

### Step 2: Configure units

Under **Field** → **Unit**, select `seconds (s)`.

### Step 3: Set explicit colours for the three series

Under **Overrides**, add an override for each series name:

| Series | Colour |
|---|---|
| P50 | Green |
| P95 | Orange |
| P99 | Red |

Click **Apply**. You should see three lines: P50 low and stable, P95 higher, P99 the most variable. The gap between P95 and P99 widens during load — this is the long tail.

> **Why the long tail matters:** If your SLO is "P95 latency < 200ms", you are implicitly accepting that the worst 5% of requests may be much slower. Understanding the P99 tells you how bad that tail is. A service with P50=10ms, P95=100ms, P99=2000ms has a serious tail problem even though P95 looks fine.

---

## Challenge 6 — Add a template variable for endpoint filtering

Template variables turn one static dashboard into a dynamic one. Instead of one panel per endpoint, you have one panel with a dropdown.

### Step 1: Open dashboard settings

In the dashboard you created, click the **gear icon** (⚙) at the top right → **Variables** → **Add variable**.

### Step 2: Configure the variable

| Field | Value |
|---|---|
| **Variable type** | Query |
| **Name** | `endpoint` |
| **Label** | `Endpoint` |
| **Data source** | Prometheus |
| **Query** | `label_values(lumio_http_requests_total, endpoint)` |
| **Multi-value** | Off (single selection for now) |
| **Include All option** | On |

Click **Update**. A dropdown now appears at the top of the dashboard with all endpoint names.

### Step 3: Use the variable in a query

Edit your request rate panel. Change the query to:

```
sum(rate(lumio_http_requests_total{endpoint="$endpoint"}[5m])) by (status_code)
```

`$endpoint` interpolates the variable value. When you select `ingest_event` from the dropdown, the panel filters to only that endpoint's traffic — broken down by status code.

> **Why variables beat hardcoded labels:** As the service grows, new endpoints appear. A dashboard with hardcoded endpoint names needs manual updates. A dashboard using `label_values()` discovers new endpoints automatically.

---

## Challenge 7 — Export and study the dashboard JSON

Understanding the dashboard JSON format makes you independent of the UI. You can write dashboards as code, review them in PRs, and deploy them via provisioning.

### Step 1: Export a dashboard you built

In any dashboard, click the **Share** icon (or go to Dashboard settings → JSON model).

Copy the JSON and compare it to `phase-1-grafana/app/grafana/dashboards/lumio-overview.json`.

### Step 2: Find a panel object in the JSON

```bash
cat phase-1-grafana/app/grafana/dashboards/lumio-overview.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['panels'][0], indent=2))"
```

Walk through the structure:
- `"type": "timeseries"` — visualisation type
- `"targets"` — array of queries (each has `"expr"` and `"legendFormat"`)
- `"fieldConfig.defaults.unit"` — axis unit
- `"fieldConfig.defaults.thresholds"` — threshold steps
- `"gridPos"` — `x`, `y`, `w`, `h` on the 24-column grid

### Step 3: Make a change and see it ignored

Edit the dashboard title in the Grafana UI (click the title, rename it). Refresh the page. The original title is restored.

This is `allowUiUpdates: false` in action. The provisioned JSON is the source of truth. To rename the dashboard, edit the JSON file and restart Grafana (or wait for the `updateIntervalSeconds: 30` reload).

This is the correct behaviour for production: the repository is the source of truth, not the UI.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start the full stack (api + prometheus + grafana) |
| `docker compose logs -f grafana` | Stream Grafana startup logs |
| `docker compose restart grafana` | Reload provisioned dashboards after editing JSON files |
| `docker compose down -v` | Remove all containers and volumes (resets Grafana state) |

| Grafana action | Where |
|---|---|
| Browse dashboards | Dashboards → Browse |
| Create new dashboard | + → New dashboard |
| Add panel | Dashboard → Add → Visualization |
| Dashboard variables | Dashboard → ⚙ Settings → Variables |
| Export dashboard JSON | Dashboard → Share → Export |
| View provisioned datasources | Connections → Data sources |

---

## What this doesn't do yet

| Issue | Impact | Fixed in |
|---|---|---|
| No alerting — dashboards are passive | Team still learns about incidents from customers | Phase 2 |
| No business context | "High latency" but which customer? Which event type? | Phase 3 |
| Logs not in Grafana | Two-tool context switch during incidents: Grafana + SSH | Phase 4 |
| No log-to-metric correlation | See the spike, can't jump to the log line that caused it | Phase 5 |
| Dashboards exist only for one service | No standardised dashboards across services | Phase 9 |

---

## Production considerations

### 1. Dashboards as code is not optional
A dashboard that only exists in Grafana's SQLite database is one `docker compose down -v` away from being lost. Store every dashboard as JSON in git. Use provisioning to load it. This also means dashboards go through PR review — a panel that removes the error rate threshold gets caught before it reaches production.

### 2. Use deterministic datasource UIDs
When you create a datasource manually in the Grafana UI, it gets a random UID. When you reference that datasource in dashboard JSON and move the dashboard to another Grafana instance, the UID won't match. Always provision datasources with an explicit `uid:` field (as in this lab: `uid: prometheus`). Use the same UID in every environment.

### 3. Set sensible refresh intervals
`refresh: "30s"` is appropriate for operational dashboards. Avoid `5s` or `10s` for dashboards with expensive histogram quantile queries — each refresh fires all queries simultaneously, and at high cardinality this causes Prometheus query latency to spike. Use `1m` or `5m` for dashboards that aren't actively being watched during an incident.

### 4. Mount a volume for Grafana's SQLite database
Even with provisioned dashboards, Grafana stores user sessions, preferences, and annotations in `/var/lib/grafana/grafana.db`. Mount this as a volume (as in this lab: `grafana_data:/var/lib/grafana`) to persist state across container restarts. Without it, every restart logs everyone out.

### 5. Never enable anonymous access in production
The `GF_AUTH_ANONYMOUS_ENABLED=true` setting is common in tutorials. It means anyone who can reach port 3000 can read all dashboards and execute arbitrary PromQL queries against your metrics backend. In production, use Grafana's organisation and team model, or integrate with your SSO provider (OIDC, SAML).

---

## Outcome

The Lumio team now has a shared operational dashboard that loads in one click, requires no PromQL knowledge to read, and updates every 30 seconds. The pre-built dashboard covers the RED method (rate, errors, duration) plus business metrics (events by type). New engineers joining the team can understand the service's health in under 5 minutes.

The dashboard is version-controlled as JSON, deployed via Grafana provisioning, and cannot be accidentally modified in the UI. It is the single source of truth for what "healthy" looks like.

Nobody is alerted when it stops looking healthy. That is fixed in Phase 2.

---

[Back to Phase 0](../phase-0-first-metrics/README.md) | [Next: Phase 2 — Alerting with Alertmanager →](../phase-2-alerting/README.md)
