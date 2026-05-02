#!/usr/bin/env bash
# cardinality.sh — generate high-cardinality load to trigger the series explosion.
#
# Sends requests to the API with a unique X-User-ID header on every request.
# Each unique user_id × endpoint × method × status_code combination creates
# a new time series in Prometheus.
#
# With 10 workers running continuously, new series are created at ~100/sec.
# Watch prometheus_tsdb_head_series rise at:
#   http://localhost:9090/graph?g0.expr=prometheus_tsdb_head_series
#
# Usage:
#   ./cardinality.sh          # 10 concurrent workers (default)
#   ./cardinality.sh 25       # 25 concurrent workers (faster explosion)
#
# Press Ctrl+C to stop.

CONCURRENCY=${1:-10}
API=http://localhost:8000
ENDPOINTS=("/events" "/events/summary" "/health")

echo "Starting cardinality load: $CONCURRENCY workers, unique X-User-ID per request"
echo ""
echo "Monitor series growth:"
echo "  Prometheus UI: http://localhost:9090/graph?g0.expr=prometheus_tsdb_head_series"
echo "  TSDB status:   http://localhost:9090/tsdb-status"
echo "  Grafana:       http://localhost:3000/d/lumio-cardinality"
echo ""
echo "Press Ctrl+C to stop."
echo ""

trap 'echo ""; echo "Stopped. Final series count:"; curl -sg "http://localhost:9090/api/v1/query?query=prometheus_tsdb_head_series" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r[\"data\"][\"result\"][0][\"value\"][1] if r[\"data\"][\"result\"] else \"unavailable")\"; kill 0' INT

worker() {
  local id=$1
  local counter=0
  while true; do
    # Generate a unique user ID — RANDOM gives 0–32767, combining two gives
    # up to ~1 billion unique values, enough for a sustained explosion.
    local user_id="user_$(( RANDOM * 32768 + RANDOM ))"
    local endpoint=${ENDPOINTS[$((RANDOM % ${#ENDPOINTS[@]}))]}

    if [[ "$endpoint" == "/events" ]]; then
      curl -s -X POST "$API$endpoint" \
        -H "X-User-ID: $user_id" \
        -H "Content-Type: application/json" \
        -d '{"event": "page_view"}' > /dev/null
    else
      curl -s "$API$endpoint" \
        -H "X-User-ID: $user_id" > /dev/null
    fi

    counter=$(( counter + 1 ))
    # Print progress every 100 requests per worker
    if (( counter % 100 == 0 )); then
      echo "  worker-$id: $counter requests sent"
    fi
  done
}

for i in $(seq 1 "$CONCURRENCY"); do
  worker "$i" &
done

wait
