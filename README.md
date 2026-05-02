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
| 3 | 20 | Requests are slow — but which endpoint, and why? |
| 4 | 25 | Metrics say something is wrong but logs are scattered across hosts |
| 5 | 30 | Found the error in the logs but can't correlate it to the spike in the graph |
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
| 2 | Alerting with Alertmanager | Beginner–Intermediate | 2–3 hrs | 🔜 Coming soon |
| 3 | Application Instrumentation | Intermediate | 3–4 hrs | 🔜 Coming soon |
| 4 | Log Aggregation with Loki | Intermediate | 3–4 hrs | 🔜 Coming soon |
| 5 | Correlating Logs and Metrics | Intermediate–Advanced | 3–4 hrs | 🔜 Coming soon |
| 6 | Recording Rules & Query Optimisation | Advanced | 2–3 hrs | 🔜 Coming soon |
| 7 | Infrastructure Metrics & Exporters | Advanced | 3–4 hrs | 🔜 Coming soon |
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
├── phase-2-alerting/             (coming soon)
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
