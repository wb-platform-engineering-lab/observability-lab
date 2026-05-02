#!/usr/bin/env bash
# load.sh — generate realistic traffic against both environments.
#
# Usage:
#   ./load.sh          # load both dev and prod (default)
#   ./load.sh dev      # load dev only
#   ./load.sh prod     # load prod only
#
# Each environment gets its own traffic loop so you can observe them
# independently on the Grafana env-aware dashboard.

TARGET=${1:-both}

DEV_API=http://localhost:8001
PROD_API=http://localhost:8000

ENDPOINTS=("/events" "/events" "/events" "/events/summary" "/health")

traffic_loop() {
  local name=$1
  local api=$2
  local delay=$3   # seconds between requests — lower = higher RPS
  while true; do
    endpoint=${ENDPOINTS[$((RANDOM % ${#ENDPOINTS[@]}))]}
    if [[ "$endpoint" == "/events" ]]; then
      curl -s -X POST "$api$endpoint" \
        -H "Content-Type: application/json" \
        -d '{"event": "page_view"}' > /dev/null
    else
      curl -s "$api$endpoint" > /dev/null
    fi
    sleep "$delay"
  done
}

case "$TARGET" in
  dev)
    echo "Sending traffic to dev ($DEV_API) — press Ctrl+C to stop"
    traffic_loop dev "$DEV_API" 0.5 &
    traffic_loop dev "$DEV_API" 0.5 &
    traffic_loop dev "$DEV_API" 1.0 &
    ;;
  prod)
    echo "Sending traffic to prod ($PROD_API) — press Ctrl+C to stop"
    traffic_loop prod "$PROD_API" 0.3 &
    traffic_loop prod "$PROD_API" 0.3 &
    traffic_loop prod "$PROD_API" 0.3 &
    traffic_loop prod "$PROD_API" 0.5 &
    ;;
  both|*)
    echo "Sending traffic to both environments — press Ctrl+C to stop"
    echo "  dev  → $DEV_API"
    echo "  prod → $PROD_API"
    # Prod gets slightly higher RPS — realistic traffic differential
    traffic_loop dev  "$DEV_API"  0.5 &
    traffic_loop dev  "$DEV_API"  0.8 &
    traffic_loop prod "$PROD_API" 0.2 &
    traffic_loop prod "$PROD_API" 0.2 &
    traffic_loop prod "$PROD_API" 0.3 &
    traffic_loop prod "$PROD_API" 0.5 &
    ;;
esac

wait
