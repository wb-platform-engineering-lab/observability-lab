#!/usr/bin/env bash
# break.sh — set the error rate on a specific environment.
#
# Usage:
#   ./break.sh dev  0.5    # 50% errors in dev
#   ./break.sh prod 0.5    # 50% errors in prod
#   ./break.sh dev  0.0    # reset dev to 0% errors
#   ./break.sh prod 0.0    # reset prod to 0% errors
#
# Phase 9 lesson: run ./break.sh dev 0.5 and watch ONLY the dev-receiver
# webhook get notified.  Then run ./break.sh prod 0.5 and see that the
# prod-receiver gets paged quickly with a short repeat interval.
#
# The same alert rule fires for both — Alertmanager routes them differently
# based on the env label.

ENV=${1:-prod}
RATE=${2:-0.5}

case "$ENV" in
  dev)  API=http://localhost:8001 ;;
  prod) API=http://localhost:8000 ;;
  *)
    echo "Usage: $0 <dev|prod> [rate]"
    exit 1
    ;;
esac

echo "Setting error rate on $ENV to $(echo "$RATE * 100" | bc)%"
curl -s -X POST "$API/admin/set-error-rate" \
  -H "Content-Type: application/json" \
  -d "{\"rate\": $RATE}" | python3 -m json.tool

echo ""
echo "Watch the correct receiver get notified:"
if [[ "$ENV" == "dev" ]]; then
  echo "  docker compose logs -f webhook-dev"
  echo "  (webhook-prod should stay silent)"
else
  echo "  docker compose logs -f webhook-prod"
  echo "  (webhook-dev should stay silent)"
fi
echo ""
echo "Reset: ./break.sh $ENV 0.0"
