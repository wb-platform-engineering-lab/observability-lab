# The Lumio Story

---

Lumio started as a four-person team in a co-working space in Lyon.

The product was simple: a real-time behavioural analytics API for e-commerce companies. Retailers sent events — page views, cart additions, checkout starts, search queries — and Lumio returned enriched payloads in milliseconds. Personalisation engines, fraud detection, live inventory signals. The kind of thing that used to require a data warehouse and a Monday morning report, now delivered as a low-latency API call.

The MVP took six weeks. A Python API, a Redis queue, a PostgreSQL store. It ran fine on the founders' laptops. The second engineer joined and spent three days debugging environment mismatches. The third joined and hit the same wall.

They containerised everything. After that, the stack ran identically everywhere — developer laptops, CI runners, the single production server.

For three months, things were quiet. The API handled the load. Customers were happy. The team grew to twelve engineers.

Then the first real incident happened.

---

At 2:14am on a Tuesday, Lumio's largest customer — a French fashion retailer processing €2 million per day — opened a support ticket: *"Your API is returning 503s. We're losing €40,000 per hour in abandoned checkout completions."*

The on-call engineer woke up, opened his laptop, and SSHed into the production host. He ran `docker logs lumio-api`. He saw errors — upstream timeouts, connection resets — but the logs told him nothing useful. When did the errors start? How many per minute? Which endpoint? Was it getting better or worse?

He had no idea. He could read the present, not the past.

He restarted the container. The errors stopped. He went back to sleep.

The next morning the post-mortem had one line:

*"Root cause: unknown. Resolution: container restart."*

The CTO looked at that line for a long time. Then she said:

*"We need to be able to see what happened. Not just what's happening right now — what was happening an hour ago, a day ago. We need numbers over time. Rate of requests. Rate of errors. Latency at the 95th percentile. We need Prometheus."*

---

Each phase of this lab is motivated by a real observability problem that emerged as Lumio scaled:

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
