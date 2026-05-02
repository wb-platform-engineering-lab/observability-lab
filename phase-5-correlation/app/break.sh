#!/usr/bin/env bash
RATE=${1:-0.5}
API=http://localhost:8000
echo "Setting error rate to $(echo "$RATE * 100" | bc)%"
curl -s -X POST "$API/admin/set-error-rate" \
  -H "Content-Type: application/json" \
  -d "{\"rate\": $RATE}" | python3 -m json.tool
echo ""
echo "Correlation workflow:"
echo "  1. Watch error rate rise in Grafana → Lumio API — Full Stack"
echo "  2. Open Explore → Loki: {service=\"api\", level=\"WARNING\"}"
echo "  3. Click a log line → expand → click 'Open trace in Tempo'"
echo "  4. In the trace, click 'Logs' on the process-event span"
