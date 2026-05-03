#!/usr/bin/env bash
# load.sh — generate realistic traffic against the Lumio API
#
# Usage:
#   ./load.sh            # run until Ctrl-C
#   ./load.sh 60         # run for 60 seconds then exit
#
# Endpoints and weights:
#   /health        10%   — liveness probe traffic
#   /ingest        50%   — primary write path
#   /query         30%   — read path
#   /dashboard     10%   — UI-facing endpoint
#
# Traffic shape:
#   - Random inter-request sleep 50–250 ms (≈ 5–20 RPS)
#   - Random client IDs in X-Client-ID header (realistic but bounded — 10 IDs)
#   - Requests are sequential; add & background to parallelise if needed

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
DURATION="${1:-0}"   # 0 = run forever

CLIENT_IDS=(client-alpha client-beta client-gamma client-delta client-epsilon
            client-zeta client-eta client-theta client-iota client-kappa)

start_time=$(date +%s)

echo "Sending load to ${API_URL}  (duration: ${DURATION}s, 0=forever)"
echo "Press Ctrl-C to stop."
echo ""

request_count=0

while true; do
  if [[ "${DURATION}" -gt 0 ]]; then
    elapsed=$(( $(date +%s) - start_time ))
    if [[ "${elapsed}" -ge "${DURATION}" ]]; then
      echo ""
      echo "Done. Sent ${request_count} requests in ${elapsed}s."
      exit 0
    fi
  fi

  # Pick a random endpoint by weight
  roll=$(( RANDOM % 100 ))
  if   [[ $roll -lt 10 ]]; then endpoint="/health"
  elif [[ $roll -lt 60 ]]; then endpoint="/ingest"
  elif [[ $roll -lt 90 ]]; then endpoint="/query"
  else                          endpoint="/dashboard"
  fi

  # Pick a random client
  client="${CLIENT_IDS[$(( RANDOM % ${#CLIENT_IDS[@]} ))]}"

  # Fire the request (suppress output; print a dot per request)
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-Client-ID: ${client}" \
    "${API_URL}${endpoint}" 2>/dev/null || echo "000")

  # Colour-code the dot: green=2xx, yellow=4xx, red=5xx/err
  case "${http_code:0:1}" in
    2) printf "\033[32m.\033[0m" ;;
    4) printf "\033[33m.\033[0m" ;;
    *) printf "\033[31mE\033[0m" ;;
  esac

  request_count=$(( request_count + 1 ))
  # Print request count every 50 requests
  if (( request_count % 50 == 0 )); then
    echo " [${request_count}]"
  fi

  # Random sleep 50–250 ms
  sleep "0.$(( 50 + RANDOM % 200 ))"
done
