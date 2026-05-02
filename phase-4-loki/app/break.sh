#!/usr/bin/env bash
RATE=${1:-0.5}
API=http://localhost:8000
echo "Setting error rate to $(echo "$RATE * 100" | bc)%"
curl -s -X POST "$API/admin/set-error-rate" \
  -H "Content-Type: application/json" \
  -d "{\"rate\": $RATE}" | python3 -m json.tool
echo ""
echo "Watch error logs appear in Grafana → Lumio API — Logs + Metrics"
echo "or in Grafana Explore → Loki: {service=\"api\", level=\"WARNING\"}"
