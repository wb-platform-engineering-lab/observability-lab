# Phase 7 — Infrastructure Metrics & Exporters

> **Lumio, 50 engineers.**
> It's 3am.  The Lumio API dashboard shows green — error rate 1%, P95 latency 80ms, no alerts firing.  But customers are reporting timeouts.  The on-call engineer opens Grafana and stares at the app dashboard for two minutes before thinking to SSH into the host.  `df -h` returns `98%`.  Prometheus can't write its WAL.  Queries are timing out.  The API is fine.  The host is dying.  The post-mortem action item: *"we have zero visibility into the machine our software runs on."*

## What you will build

Two new components added to the Phase 6 stack:

| Component | What it exposes | Port |
|---|---|---|
| **node_exporter** | Host OS metrics: CPU, memory, disk, filesystem, network | 9100 |
| **cAdvisor** | Per-container resource metrics: CPU, memory, filesystem, network | 8080 |

Two new Prometheus rule files:
- `infra_recording.yml` — pre-computed infrastructure metrics following Phase 6's naming convention
- `infra_alerting.yml` — threshold and predictive (predict_linear) alerts

One new Grafana dashboard — **Lumio Infrastructure** — alongside the existing API dashboard.

---

## Concepts

### The gap in application-only observability

The metrics from Phases 0–6 are all *push-pull instrumented* — the application code explicitly creates and updates them (`Counter`, `Histogram`, `Gauge`).  This means:

- You can see that requests are succeeding
- You cannot see that the disk holding the database write-ahead log is 98% full
- You cannot see that a memory leak in a container is slowly consuming the host's RAM
- You cannot see that a noisy-neighbour container is consuming 80% of CPU

Exporters bridge this gap.  They translate existing system-level data sources (OS kernel counters, Docker daemon APIs) into the Prometheus exposition format.

### What an exporter is

An exporter is a process that:
1. Reads metrics from a target system (Linux `/proc`, Docker daemon, MySQL, Redis, etc.)
2. Translates them into Prometheus format
3. Exposes a `/metrics` HTTP endpoint

Prometheus scrapes the exporter the same way it scrapes your application.  The exporter doesn't push to Prometheus — it waits to be scraped.

```
┌──────────┐  HTTP /metrics   ┌──────────────┐  scrape   ┌────────────┐
│  Linux   │ ──────────────→  │ node_exporter│ ←──────── │ Prometheus │
│  /proc   │                  │ :9100        │           └────────────┘
└──────────┘                  └──────────────┘

┌──────────┐  Docker API      ┌──────────────┐  scrape   ┌────────────┐
│  Docker  │ ──────────────→  │   cAdvisor   │ ←──────── │ Prometheus │
│  daemon  │                  │ :8080        │           └────────────┘
└──────────┘                  └──────────────┘
```

### node_exporter

node_exporter is the standard Prometheus exporter for Linux host metrics.  It reads from `/proc` and `/sys` — the Linux kernel's virtual filesystems that expose everything about the system's current state.

Key metrics and what they tell you:

| Metric | Type | Meaning |
|---|---|---|
| `node_cpu_seconds_total` | Counter | CPU time spent in each mode (user/system/iowait/idle/steal/etc) per CPU |
| `node_memory_MemAvailable_bytes` | Gauge | Kernel estimate of available memory including reclaimable cache |
| `node_memory_MemTotal_bytes` | Gauge | Total physical RAM |
| `node_filesystem_avail_bytes` | Gauge | Bytes available to non-root users on each mounted filesystem |
| `node_filesystem_size_bytes` | Gauge | Total size of each mounted filesystem |
| `node_disk_read_bytes_total` | Counter | Bytes read from each block device |
| `node_disk_written_bytes_total` | Counter | Bytes written to each block device |
| `node_network_receive_bytes_total` | Counter | Bytes received on each network interface |
| `node_network_transmit_bytes_total` | Counter | Bytes transmitted on each network interface |
| `node_load1` / `node_load5` / `node_load15` | Gauge | System load average |

**On Docker Desktop (Mac/Windows):** node_exporter runs inside the Linux VM that Docker Desktop creates.  It exposes the VM's metrics, not your Mac's.  This is still useful — the VM is what your containers actually run on — but disk usage reflects the VM's virtual disk, not your Mac's SSD.

### cAdvisor

cAdvisor (Container Advisor) is a Google-maintained exporter for container resource metrics.  It connects to the Docker daemon and reads cgroup data for each running container.

Key metrics:

| Metric | Type | Meaning |
|---|---|---|
| `container_cpu_usage_seconds_total` | Counter | CPU seconds consumed by each container |
| `container_memory_usage_bytes` | Gauge | Memory used by each container (RSS + cache) |
| `container_fs_usage_bytes` | Gauge | Container filesystem usage |
| `container_network_receive_bytes_total` | Counter | Network bytes received per container |

cAdvisor metrics have high cardinality — labels include `id` (a 64-character cgroup path), `image`, `name`, `pod`, and others.  The `metric_relabel_configs` in `prometheus.yml` drops the metrics we don't need, and the recording rules in `infra_recording.yml` aggregate down to just the `name` label.

### CPU utilisation from a counter

`node_cpu_seconds_total` is a counter — it monotonically increases.  To get a utilisation rate you need `rate()`:

```promql
# Fraction of time NOT idle, averaged across all CPUs
1 - avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m]))
```

The `mode` label distinguishes:
- **user** — CPU running application code (your processes)
- **system** — CPU running kernel code (system calls, interrupts)
- **iowait** — CPU waiting for disk I/O to complete (processes blocked on reads/writes)
- **steal** — CPU cycles stolen by the hypervisor (virtualisation overhead)
- **idle** — CPU doing nothing

An iowait spike means processes are blocked on disk, not on the CPU — the fix is faster storage, not more CPU.  A system spike means kernel overhead — investigate system calls.

### predict_linear: alerting before problems happen

`predict_linear(v[d], t)` takes a range vector `v` over duration `d`, fits a linear regression, and returns the predicted value `t` seconds in the future.

```promql
# Will the filesystem run out of space in the next 4 hours, based on the last hour's trend?
predict_linear(node_filesystem_avail_bytes{mountpoint="/"}[1h], 4 * 3600) < 0
```

This alert fires when the extrapolated trend reaches zero within 4 hours.  Compare this to a threshold alert:

| Alert type | When it fires | Lead time |
|---|---|---|
| Threshold `> 95%` | When disk is already 95% full | Minutes to hours |
| `predict_linear < 0` in 4h | While disk is still e.g. 60% full but filling fast | Hours |

The predictive alert gives you time to act.  The threshold alert is a safety net for when the trend changes suddenly.  Use both.

### The `instance` label and multi-host scraping

Every Prometheus metric has an `instance` label set to the `host:port` of the scrape target.  For a single host this is just a tag.  When you add a second host you add a second target to the scrape config, and the `instance` label lets you tell them apart:

```yaml
scrape_configs:
  - job_name: node
    static_configs:
      - targets:
          - "web-01.prod.lumio.io:9100"
          - "web-02.prod.lumio.io:9100"
          - "worker-01.prod.lumio.io:9100"
```

All alerts and recording rules use `by(instance)` aggregations so per-host results stay separate.  In Phase 9 (Multi-environment) you will see how to add an `env` label to separate production from staging.

---

## Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  docker compose up                                                   │
│                                                                      │
│  api:8000          ←── load.sh                                       │
│  node-exporter:9100  ←── reads /proc /sys (Linux VM on Mac)          │
│  cadvisor:8080       ←── reads Docker daemon cgroups                 │
│  prometheus:9090   ←── scrapes all three + itself                    │
│  alertmanager:9093                                                   │
│  webhook:5001      ←── receives infrastructure alerts                │
│  grafana:3000      ←── two dashboards: Lumio API + Infrastructure    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Challenges

### Challenge 1 — Start the stack

```bash
cd phase-7-exporters/app
docker compose up --build -d
```

Verify all targets are up:
- Prometheus targets: http://localhost:9090/targets

You should see five jobs: `lumio-api`, `node`, `cadvisor`, `prometheus` — all showing `UP`.

If `node` shows `DOWN`:
- On Linux: check that the node-exporter container has `network_mode: host` and can bind to 9100
- On Docker Desktop: `host.docker.internal` should resolve — verify with `docker compose exec prometheus wget -qO- http://host.docker.internal:9100/metrics | head`

Start the load generator:
```bash
./load.sh
```

---

### Challenge 2 — Explore raw node_exporter metrics

Open the Prometheus UI: http://localhost:9090

In the expression input, try:

```promql
# All node_exporter metrics — 500+ series
{job="node"}

# CPU time broken down by mode
node_cpu_seconds_total{job="node"}

# Current filesystem free space
node_filesystem_avail_bytes{job="node", mountpoint="/"}

# Memory available
node_memory_MemAvailable_bytes{job="node"}
```

Notice that `node_cpu_seconds_total` has one series per CPU per mode.  On a 4-core VM there are ~28 series (4 cores × 7 modes).  Understanding how counters accumulate helps you write correct `rate()` expressions.

**Question:** Why does `node_memory_MemAvailable_bytes` sometimes exceed `node_memory_MemFree_bytes + node_memory_Buffers_bytes + node_memory_Cached_bytes`?

> The kernel can reclaim memory from slab allocators (dentry/inode caches) that are not reflected in the Buffers/Cached values.  MemAvailable is more accurate.

---

### Challenge 3 — Verify recording rules

In the Prometheus UI go to **Status → Rules**: http://localhost:9090/rules

You should see two rule groups:
- `lumio.api.recording` — 9 rules from Phase 6
- `lumio.infra.recording` — 11 rules from this phase

All should be in state `ok`.  Query a few:

```bash
# CPU utilisation
curl -sg 'http://localhost:9090/api/v1/query?query=instance:node_cpu_utilization:ratio5m' \
  | python3 -m json.tool

# Disk utilisation
curl -sg 'http://localhost:9090/api/v1/query?query=instance_mountpoint:node_filesystem_utilization:ratio' \
  | python3 -m json.tool

# Container memory
curl -sg 'http://localhost:9090/api/v1/query?query=container_name:container_memory_usage_bytes:avg' \
  | python3 -m json.tool
```

---

### Challenge 4 — Explore the Infrastructure dashboard

Open the **Lumio Infrastructure** dashboard: http://localhost:3000/d/lumio-infra

Walk through each section:

**Host Overview row** — four stat panels at a glance:
- CPU Utilisation — should be low at idle, green
- Memory Utilisation — depends on the VM size; typically 40–60%
- Disk Utilisation (/) — the VM's root filesystem
- Disk Fill ETA — should show "stable" (no trend toward filling)

**CPU row** — two panels:
- CPU Time by Mode (stacked) — user + system + iowait should sum to the total utilisation
- CPU Utilisation (overall) — confirms the stacked view

**Memory row** — used vs available breakdown

**Disk row** — three panels:
- Filesystem Usage (bar gauge per mountpoint)
- Disk I/O Throughput (read/write bytes/sec)
- Disk Free Space Over Time (watch this during Challenge 5)

**Network row** — receive/transmit on external interfaces

**Containers row** (cAdvisor) — CPU and memory per Docker container

---

### Challenge 5 — Simulate disk pressure with fill_disk.sh

> **Note:** On Docker Desktop the disk is the Linux VM's virtual disk (typically 60–100 GB).  Running `fill_disk.sh` writes real data to it.  It will be visible to node_exporter.  Always run `./fill_disk.sh clear` when done.

```bash
# Write 1 GB of zeros to the Prometheus data volume
./fill_disk.sh 1000
```

Watch the **Disk Free Space Over Time** panel on the Infrastructure dashboard.  You should see a step-down in the available bytes as the fill runs.

Now observe `predict_linear` in action:

```promql
predict_linear(
  instance_mountpoint:node_filesystem_avail_bytes:current{mountpoint="/"}[5m],
  4 * 3600
)
```

With only a few minutes of history the prediction will be volatile.  After 10–15 minutes of steady write activity it stabilises.  If the predicted value goes negative, `NodeDiskFilling` will fire.

You can also query the raw prediction:
```bash
curl -sg 'http://localhost:9090/api/v1/query?query=predict_linear(instance_mountpoint:node_filesystem_avail_bytes:current%7Bmountpoint%3D%22%2F%22%7D%5B10m%5D%2C+4+*+3600)' \
  | python3 -m json.tool
```

Clear the fill:
```bash
./fill_disk.sh clear
```

---

### Challenge 6 — Understand the alerting rules

Open `prometheus/rules/infra_alerting.yml`.  There are four infrastructure alert families:

1. **NodeDown** — node_exporter unreachable for 1m
2. **NodeDiskSpaceLow / NodeDiskSpaceCritical** — threshold-based at 85% and 95%
3. **NodeDiskFilling** — predictive using `predict_linear`, fires when trend will fill in < 4h
4. **NodeMemoryPressure / NodeMemoryCritical** — memory above 90% / 97%
5. **NodeHighCPU** — CPU above 90% for 10 minutes

Key observation: the threshold alert (`NodeDiskSpaceCritical > 0.95`) fires when you're already in trouble.  The predictive alert (`NodeDiskFilling`) fires before you're in trouble.  In production you'd use both:

- The predictive alert pages someone during business hours to investigate
- The threshold alert pages someone urgently at 3am when the trend was missed or suddenly accelerated

**Check active alerts:**

In the Prometheus UI, go to **Alerts**: http://localhost:9090/alerts

`LumioWatchdog` should be firing (expected — it always fires).  All infrastructure alerts should be green unless the fill_disk simulation triggered them.

---

### Challenge 7 — Connect the two signals

The power of having both application and infrastructure metrics in the same Prometheus instance is that you can correlate them in a single query.

A latency spike can be caused by:
- Application errors (→ check error rate in `lumio-api` dashboard)
- Memory pressure causing GC pauses (→ check memory utilisation in `infra` dashboard)
- CPU saturation (→ check CPU in `infra` dashboard)
- Disk I/O wait (→ check iowait in CPU mode breakdown)

Try correlating by opening both dashboards side by side and running `break.sh`:

```bash
./break.sh 0.5   # 50% error rate
```

Notice that the API dashboard shows the error rate spike, but the infrastructure dashboard remains flat — this confirms the problem is in the application layer, not the host.

Now imagine the reverse: disk fills → Prometheus WAL writes fail → Prometheus query timeouts → API dashboard stops updating → engineers assume no data means no problem.

This is the incident pattern Phase 7 was written to prevent.

---

### Challenge 8 — Understand the metric_relabel_configs for cAdvisor

Open `prometheus/prometheus.yml` and find the `cadvisor` job's `metric_relabel_configs`.

cAdvisor exposes hundreds of metrics per container with many label combinations.  Without filtering, scraping cAdvisor adds thousands of series per container.  The relabelling does two things:

1. **`action: keep`** — only keeps the five container metric families we use in this lab
2. **`action: drop`** (empty container name) — drops the cgroup root, which isn't a container

Verify the effect:

```bash
# Total series with cAdvisor label
curl -sg 'http://localhost:9090/api/v1/label/job/values' | python3 -m json.tool

# How many series are from cadvisor?
curl -sg 'http://localhost:9090/api/v1/query?query=count({job="cadvisor"})' \
  | python3 -m json.tool
```

Compare with and without the `metric_relabel_configs` by temporarily commenting them out, reloading, and re-running the count.  You'll see cardinality drop by 10–50x depending on how many containers are running.

```bash
# Reload without restart
curl -X POST http://localhost:9090/-/reload
```

---

## File structure

```
phase-7-exporters/
└── app/
    ├── docker-compose.yml              ← adds node-exporter + cadvisor
    ├── load.sh / break.sh
    ├── fill_disk.sh                    ← simulates disk pressure
    ├── api/                            ← unchanged from Phase 6
    ├── alertmanager/
    │   └── alertmanager.yml
    ├── webhook/
    ├── prometheus/
    │   ├── prometheus.yml              ← 4 scrape jobs + relabelling
    │   └── rules/
    │       ├── lumio_recording.yml     ← Phase 6 app recording rules
    │       ├── infra_recording.yml     ← host + container recording rules
    │       ├── lumio_alerting.yml      ← Phase 6 app alerting rules
    │       └── infra_alerting.yml      ← threshold + predictive infra alerts
    └── grafana/
        ├── provisioning/
        └── dashboards/
            ├── lumio-api.json          ← application metrics dashboard
            └── infra-overview.json     ← infrastructure dashboard
```

---

## Command reference

```bash
# Start
docker compose up --build -d

# Generate API traffic
./load.sh

# Trigger application errors
./break.sh 0.5
./break.sh 0.0   # reset

# Simulate disk pressure
./fill_disk.sh 1000   # write 1 GB
./fill_disk.sh clear  # remove fill files

# Reload Prometheus config (after editing rule files)
curl -X POST http://localhost:9090/-/reload

# Query a recording rule
curl -sg 'http://localhost:9090/api/v1/query?query=instance:node_cpu_utilization:ratio5m' \
  | python3 -m json.tool

# Count active series per job
curl -sg 'http://localhost:9090/api/v1/query?query=count({__name__!=""})+by+(job)' \
  | python3 -m json.tool

# Stop
docker compose down -v
```

---

## What this doesn't do yet

| Gap | Next phase |
|---|---|
| Cardinality from cAdvisor labels could blow up in production | Phase 8 — Cardinality & Production Pitfalls |
| Infrastructure alerts fire in dev the same as prod | Phase 9 — Multi-environment Observability |
| node_exporter only covers one node; no discovery for N hosts | Phase 9 — adds environment labels and file-based discovery |

---

## Production considerations

**Deploy node_exporter on every host.** In Kubernetes this is a DaemonSet.  On bare metal or VMs, automate with Ansible or a provisioning tool.  Add all instances to the Prometheus scrape config under `job_name: node`.

**Use file-based discovery for dynamic fleets.** Instead of hardcoding targets, use `file_sd_configs` to load a JSON/YAML file that's updated by your provisioning system when hosts join or leave the fleet.

**Set container memory limits.** cAdvisor reports `container_memory_usage_bytes` but alerting on it requires knowing the limit.  Set `--memory` limits on every container so `container_memory_usage_bytes / container_spec_memory_limit_bytes` gives you a 0–1 utilisation ratio.

**Do not alert on CPU spikes.** CPU is elastic — a 3-second spike at 100% is normal.  Only alert when saturation is sustained (the `for: 10m` on `NodeHighCPU` in this lab).  Conversely, do alert quickly on disk — disk fills are not self-correcting.

**predict_linear needs warm-up time.** The `[1h]` range in the alert means Prometheus needs 1 hour of history to make a meaningful prediction.  After a restart you'll get noisy predictions for the first hour.  Add `for: 10m` to avoid alert flapping during this window.
