#!/usr/bin/env bash
# fill_disk.sh — simulate disk pressure by writing large files into the
# Prometheus data volume.
#
# On Docker Desktop (Mac/Windows) node_exporter reports the Linux VM's disk.
# This script writes files into the prometheus_data volume (mounted at
# /prometheus inside the container) so the fill is visible to node_exporter.
#
# Usage:
#   ./fill_disk.sh [SIZE_MB]   # default: 500 MB
#   ./fill_disk.sh clear       # remove the fill files
#
# WARNING: This fills real disk space on your machine.  Always run
# ./fill_disk.sh clear when you are done.

SIZE_MB=${1:-500}

if [[ "$SIZE_MB" == "clear" ]]; then
  echo "Removing fill files from prometheus container..."
  docker compose exec prometheus sh -c 'rm -f /prometheus/fill_*.bin && echo "Removed"'
  echo ""
  echo "Disk space after clearing:"
  docker compose exec prometheus df -h /prometheus
  exit 0
fi

echo "Writing ${SIZE_MB} MB of fill data to the Prometheus volume..."
echo "This will be visible to node_exporter as disk usage on the VM."
echo ""

# Split into 100 MB chunks so progress is visible
CHUNKS=$(( SIZE_MB / 100 ))
REMAINDER=$(( SIZE_MB % 100 ))

for i in $(seq 1 $CHUNKS); do
  printf "  Writing chunk %d/%d (100 MB)...\n" "$i" "$CHUNKS"
  docker compose exec prometheus dd if=/dev/zero of=/prometheus/fill_${i}.bin \
    bs=1M count=100 status=none
done

if [[ $REMAINDER -gt 0 ]]; then
  printf "  Writing remainder (%d MB)...\n" "$REMAINDER"
  docker compose exec prometheus dd if=/dev/zero of=/prometheus/fill_remainder.bin \
    bs=1M count="$REMAINDER" status=none
fi

echo ""
echo "Done. Disk space now:"
docker compose exec prometheus df -h /prometheus

echo ""
echo "Watch the Grafana dashboard: http://localhost:3000/d/lumio-infra"
echo "The 'Disk Free Space Over Time' panel should show a step-down."
echo ""
echo "If predict_linear has enough history (> 5 min), NodeDiskFilling"
echo "may fire in the Alertmanager: http://localhost:9093"
echo ""
echo "To clear: ./fill_disk.sh clear"
