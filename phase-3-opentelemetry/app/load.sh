#!/usr/bin/env bash
# Generates realistic traffic for Phase 3.
# Run from the phase-3-opentelemetry/app directory.

API=http://localhost:8000
i=0

echo "Sending traffic to $API — Ctrl+C to stop"

while true; do
  curl -s -X POST "$API/events" \
    -H "Content-Type: application/json" \
    -d '{"type":"page_view","user_id":"u123"}' > /dev/null

  i=$((i + 1))

  if (( i % 5 == 0 )); then
    curl -s "$API/health" > /dev/null
  fi

  if (( i % 20 == 0 )); then
    curl -s "$API/events/summary" > /dev/null
  fi

  sleep 0.1
done
