#!/usr/bin/env bash
# break.sh — inject failures to drive burn rate alerts
#
# Usage:
#   ./break.sh fast          # 50% errors — triggers SLOFastBurn in ~3–4 min
#   ./break.sh slow          # 10% errors — triggers SLOSlowBurn in ~20 min
#   ./break.sh stop          # restore normal traffic (ERROR_RATE → 0.01)
#   ./break.sh status        # print current error rate from Prometheus
#
# How it works:
#   The Lumio API reads ERROR_RATE from its environment.  We update that env
#   var by restarting the container with docker compose up -d.  The rate is
#   written to a temporary .env override so plain `docker compose up` later
#   picks up the original value.
#
# Requirements: docker compose v2 in PATH

set -euo pipefail

COMPOSE_FILE="$(dirname "$0")/docker-compose.yml"
API_URL="${API_URL:-http://localhost:8000}"
MODE="${1:-}"

usage() {
  echo "Usage: $0 <fast|slow|stop|status>"
  exit 1
}

set_error_rate() {
  local rate="$1"
  echo "→ Setting ERROR_RATE=${rate} …"
  ERROR_RATE="${rate}" docker compose -f "${COMPOSE_FILE}" up -d api
  echo "  Done. Allow 15–30s for Prometheus to scrape the change."
}

case "${MODE}" in
  fast)
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  FAST BURN  —  50% error rate                           ║"
    echo "║  SLOFastBurn should fire within ~3–4 minutes.           ║"
    echo "║  Run ./break.sh stop to restore normal operation.       ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    set_error_rate 0.50
    echo ""
    echo "Watch the burn rate panel on the SLO dashboard:"
    echo "  http://localhost:3000/d/lumio-slo"
    echo ""
    echo "Or poll from the terminal:"
    echo "  watch -n5 \"curl -sG 'http://localhost:9090/api/v1/query' \\"
    echo "    --data-urlencode 'query=job:lumio_slo_burn_rate:rate1h{job=\"lumio-api\"}' \\"
    echo "    | jq '.data.result[0].value[1]'\""
    ;;

  slow)
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  SLOW BURN  —  10% error rate                           ║"
    echo "║  SLOSlowBurn should fire within ~20 minutes.            ║"
    echo "║  Run ./break.sh stop to restore normal operation.       ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    set_error_rate 0.10
    echo ""
    echo "Watch the 6h burn rate — it climbs slowly:"
    echo "  http://localhost:3000/d/lumio-slo"
    ;;

  stop)
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  RESTORE  —  setting ERROR_RATE back to 0.01 (1%)       ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    set_error_rate 0.01
    echo ""
    echo "Burn rate will decrease as old 5xx samples age out of the window."
    echo "  5m burn rate recovers in ~5 minutes."
    echo "  1h burn rate recovers in ~1 hour."
    ;;

  status)
    echo "Current metrics from Prometheus:"
    echo ""
    for metric in \
      "job:lumio_slo_availability:rate5m{job=\"lumio-api\"}" \
      "job:lumio_slo_burn_rate:rate5m{job=\"lumio-api\"}" \
      "job:lumio_slo_burn_rate:rate1h{job=\"lumio-api\"}"; do
      value=$(curl -sG "http://localhost:9090/api/v1/query" \
        --data-urlencode "query=${metric}" \
        2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('data',{}).get('result',[])
print(r[0]['value'][1] if r else 'no data')
" 2>/dev/null || echo "query failed")
      printf "  %-55s = %s\n" "${metric}" "${value}"
    done
    echo ""
    echo "Active SLO alerts:"
    curl -sG "http://localhost:9090/api/v1/alerts" 2>/dev/null | \
      python3 -c "
import sys, json
d = json.load(sys.stdin)
alerts = [a for a in d.get('data',{}).get('alerts',[]) if a.get('labels',{}).get('slo')]
if not alerts:
    print('  (none)')
for a in alerts:
    l = a['labels']
    print(f\"  {l.get('alertname')} severity={l.get('severity')} state={a.get('state')}\")
" 2>/dev/null || echo "  (prometheus unreachable)"
    ;;

  *)
    usage
    ;;
esac
