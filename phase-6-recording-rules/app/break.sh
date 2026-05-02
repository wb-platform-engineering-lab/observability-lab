#!/usr/bin/env bash
# Simulates an incident by setting the error rate to 50%.
# Run this while load.sh is running to trigger the alerting rules.
#
# Usage:
#   ./break.sh          — set error rate to 50%
#   ./break.sh 0.2      — set error rate to 20%
#   ./break.sh 0.02     — restore to normal (2%)

RATE=${1:-0.5}
API=http://localhost:8000

echo "Setting error rate to $(echo "$RATE * 100" | bc)%"
curl -s -X POST "$API/admin/set-error-rate" \
  -H "Content-Type: application/json" \
  -d "{\"rate\": $RATE}" | python3 -m json.tool

echo ""
echo "Watch alerts fire:"
echo "  docker compose logs -f webhook"
echo "  open http://localhost:9093  (Alertmanager UI)"
