# Observability Lab

A hands-on, phase-by-phase lab for mastering observability — from zero visibility to a production-grade monitoring platform with Prometheus, Grafana, and Loki.

Built around **Lumio** — a fictional real-time analytics SaaS — where each phase is motivated by a real operational problem the team hit as they grew.

> **New here?** Read [PRINCIPLES.md](./PRINCIPLES.md) first — the observability foundations, the three pillars, SLI/SLO design, and the seven principles applied in this lab. Then read [STORY.md](./STORY.md) for the Lumio backstory.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

---

## Tech Stack

![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-F46800?style=flat&logo=grafana&logoColor=white)
![Loki](https://img.shields.io/badge/Loki-F46800?style=flat&logo=grafana&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)
![Dynatrace](https://img.shields.io/badge/Dynatrace-1496FF?style=flat&logo=dynatrace&logoColor=white)
![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-000000?style=flat&logo=opentelemetry&logoColor=white)

---

## The Product — Lumio

Lumio is a B2B real-time behavioural analytics API. E-commerce companies send customer events (page views, cart additions, checkouts) and receive enriched, structured payloads in milliseconds — driving personalisation engines, fraud detection, and live inventory signals.

Each phase is motivated by a real observability problem that emerged as Lumio scaled:

| Phase | Engineers | Problem |
|---|---|---|
| 0 | 12 | Production crashed at 2am. Post-mortem: *"root cause unknown."* |
| 1 | 12 | Metrics exist but raw PromQL at 3am is not an incident response procedure |
| 2 | 15 | Nobody gets woken up until a customer calls |
| 3 | 20 | Requests are slow — metrics show the spike, but which line of code caused it? |
| 4 | 25 | Metrics say something is wrong but logs are scattered across hosts |
| 5 | 30 | Three signals in three tabs — still three separate investigations |
| 6 | 40 | Dashboards take 30 seconds to load — PromQL queries are too expensive |
| 7 | 50 | App is healthy but the host is running out of disk |
| 8 | 60 | Prometheus OOM killed — a label with unbounded cardinality |
| 9 | 80 | Same alerts fire in dev and prod — oncall is tuning out the noise |
| 10 | — | Capstone — full production observability platform |
| 11 | 100+ | Self-managed stack costs 2 FTE/year — is there a better way? |

---

## Progress

| Phase | Topic | Skill level | Est. time | Status |
|---|---|---|---|---|
| 0 | First Metrics with Prometheus | Beginner | 2–3 hrs | ✅ Complete |
| 1 | Grafana Dashboards | Beginner | 2–3 hrs | ✅ Complete |
| 2 | Alerting with Alertmanager | Beginner–Intermediate | 2–3 hrs | ✅ Complete |
| 3 | OpenTelemetry — Traces and the Collector | Intermediate | 3–4 hrs | ✅ Complete |
| 4 | Log Aggregation with Loki | Intermediate | 3–4 hrs | ✅ Complete |
| 5 | Correlating Logs, Metrics, and Traces | Intermediate–Advanced | 3–4 hrs | ✅ Complete |
| 6 | Recording Rules & Query Optimisation | Advanced | 2–3 hrs | ✅ Complete |
| 7 | Infrastructure Metrics & Exporters | Advanced | 3–4 hrs | ✅ Complete |
| 8 | Cardinality & Production Pitfalls | Advanced | 2–3 hrs | 🔜 Coming soon |
| 9 | Multi-environment Observability | Advanced | 3–4 hrs | 🔜 Coming soon |
| 10 | Capstone — Production Platform | Expert | 4–6 hrs | 🔜 Coming soon |
| 11 | Enterprise APM with Dynatrace | Expert | 3–4 hrs | ✅ Complete |

---

## Repository Structure

```
.
├── STORY.md
├── phase-0-first-metrics/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml
│       ├── load.sh               ← generates realistic traffic
│       ├── api/
│       │   ├── Dockerfile
│       │   ├── app.py            ← Flask API with prometheus_client
│       │   └── requirements.txt
│       └── prometheus/
│           └── prometheus.yml
├── phase-1-grafana/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml
│       ├── load.sh
│       ├── api/
│       ├── prometheus/
│       └── grafana/
│           ├── provisioning/
│           │   ├── datasources/prometheus.yml
│           │   └── dashboards/lumio.yml
│           └── dashboards/
│               └── lumio-overview.json
├── phase-2-alerting/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml
│       ├── load.sh
│       ├── break.sh                   ← triggers a simulated incident
│       ├── api/                       ← adds /admin/set-error-rate endpoint
│       ├── prometheus/rules/lumio.yml ← alerting rules
│       ├── alertmanager/
│       │   └── alertmanager.yml       ← route tree + receivers + inhibit_rules
│       └── webhook/                   ← logs received alerts to stdout
├── phase-3-opentelemetry/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml         ← api + otelcol + tempo + prometheus + grafana
│       ├── load.sh
│       ├── api/                       ← OTel SDK, OTLP gRPC only
│       ├── otelcol/config.yml         ← receivers / processors / exporters / pipelines
│       ├── tempo/tempo.yml
│       ├── prometheus/prometheus.yml  ← scrapes otelcol:8889 (not the app)
│       └── grafana/
├── phase-4-loki/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml         ← adds loki + promtail to phase-2 stack
│       ├── load.sh
│       ├── break.sh
│       ├── api/                       ← adds structured JSON logging
│       ├── loki/loki.yml
│       ├── promtail/promtail.yml      ← Docker SD + JSON pipeline stages
│       └── grafana/                   ← Loki datasource + logs+metrics dashboard
├── phase-5-correlation/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml         ← all 9 services: full unified stack
│       ├── load.sh / break.sh
│       ├── api/                       ← Phase 3 OTel + Phase 4 logging + trace_id injection
│       ├── otelcol/ tempo/ loki/ promtail/ prometheus/ alertmanager/ webhook/
│       └── grafana/
│           └── provisioning/datasources/
│               ├── tempo.yml          ← tracesToLogs + tracesToMetrics
│               └── loki.yml           ← derivedFields → Tempo link
├── phase-6-recording-rules/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml         ← api + prometheus + alertmanager + webhook + grafana
│       ├── load.sh / break.sh
│       ├── prometheus/
│       │   ├── prometheus.yml         ← scrapes api + prometheus self-monitoring
│       │   └── rules/
│       │       ├── lumio_recording.yml ← 9 recording rules (loaded first)
│       │       └── lumio_alerting.yml  ← alerting rules referencing recorded metrics
│       └── grafana/
│           └── dashboards/
│               ├── lumio-before.json  ← raw PromQL (the problem)
│               └── lumio-after.json   ← recording rules (the fix)
├── phase-7-exporters/
│   ├── README.md
│   └── app/
│       ├── docker-compose.yml         ← adds node-exporter + cAdvisor services
│       ├── load.sh / break.sh / fill_disk.sh
│       ├── prometheus/
│       │   ├── prometheus.yml         ← 4 scrape jobs + cAdvisor relabelling
│       │   └── rules/
│       │       ├── infra_recording.yml ← CPU/mem/disk/network/container recording rules
│       │       └── infra_alerting.yml  ← NodeDiskFilling (predict_linear), memory, CPU alerts
│       └── grafana/
│           └── dashboards/
│               ├── lumio-api.json     ← application metrics
│               └── infra-overview.json ← host + container infrastructure dashboard
...
└── phase-11-dynatrace/
    ├── README.md
    └── app/
        ├── docker-compose.yml         ← DT_ENDPOINT / DT_API_TOKEN via .env
        ├── load.sh
        ├── api/
        │   ├── Dockerfile
        │   ├── app.py                 ← OTel SDK: dual pipeline + tracing
        │   └── requirements.txt
        ├── prometheus/
        │   └── prometheus.yml
        └── grafana/
            ├── provisioning/
            └── dashboards/
                └── lumio-otel.json
```

---

## Prerequisites

- Docker Desktop (or Docker Engine + Docker Compose plugin on Linux) installed and running
- Basic terminal / shell familiarity
- No prior Prometheus or Grafana knowledge required for Phase 0

---

## How to use this lab

Each phase lives in its own directory with a self-contained `README.md` that includes:
- A short narrative putting the problem in context
- Concept explanations
- A step-by-step hands-on walkthrough
- A command reference
- A "what this doesn't do yet" section linking forward to the next phase
- Production considerations

Start at Phase 0 and work forward. Each phase builds directly on the previous one.
