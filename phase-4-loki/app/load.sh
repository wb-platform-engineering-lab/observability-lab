#!/usr/bin/env bash
API=http://localhost:8000
i=0
echo "Sending traffic to $API — Ctrl+C to stop"
while true; do
  curl -s -X POST "$API/events" -H "Content-Type: application/json" \
    -d '{"type":"page_view"}' > /dev/null
  i=$((i + 1))
  (( i % 5  == 0 )) && curl -s "$API/health" > /dev/null
  (( i % 20 == 0 )) && curl -s "$API/events/summary" > /dev/null
  sleep 0.1
done
