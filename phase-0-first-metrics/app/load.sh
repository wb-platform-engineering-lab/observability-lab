#!/usr/bin/env bash
# Generate realistic traffic against the Lumio API.
# Usage: ./load.sh [target]   (default: http://localhost:8000)
set -euo pipefail

TARGET="${1:-http://localhost:8000}"
TYPES=(page_view cart_add checkout search product_view)

echo "Sending traffic to $TARGET  —  Ctrl+C to stop"
echo ""

i=0
while true; do
    TYPE="${TYPES[$((i % 5))]}"

    # High-frequency: event ingestion (every iteration)
    curl -s -X POST "$TARGET/events" \
      -H "Content-Type: application/json" \
      -d "{\"type\":\"$TYPE\"}" > /dev/null

    # Medium-frequency: health checks (every 5 iterations)
    if (( i % 5 == 0 )); then
        curl -s "$TARGET/health" > /dev/null
    fi

    # Low-frequency: summary — the slow endpoint (every 20 iterations)
    if (( i % 20 == 0 )); then
        curl -s "$TARGET/events/summary" > /dev/null
    fi

    i=$((i + 1))
    sleep 0.1
done
