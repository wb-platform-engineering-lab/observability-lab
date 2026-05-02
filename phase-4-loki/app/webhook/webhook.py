"""
Minimal webhook receiver for the Phase 2 alerting lab.

Alertmanager sends a JSON POST to /alerts. This receiver logs each alert
to stdout in a human-readable format so you can observe them via:

  docker compose logs -f webhook

In production, replace this with PagerDuty, Slack, OpsGenie, or email.
"""

import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("webhook")

app = Flask(__name__)

SEVERITY_COLOUR = {
    "critical": "\033[91m",   # red
    "warning":  "\033[93m",   # yellow
    "none":     "\033[90m",   # grey
}
RESET = "\033[0m"
GREEN = "\033[92m"


@app.route("/alerts", methods=["POST"])
def receive_alerts():
    payload = request.get_json(silent=True) or {}
    alerts = payload.get("alerts", [])

    for alert in alerts:
        status    = alert.get("status", "unknown")
        name      = alert["labels"].get("alertname", "unknown")
        severity  = alert["labels"].get("severity", "unknown")
        summary   = alert.get("annotations", {}).get("summary", "")
        desc      = alert.get("annotations", {}).get("description", "").strip()
        starts_at = alert.get("startsAt", "")
        ends_at   = alert.get("endsAt", "")

        colour = GREEN if status == "resolved" else SEVERITY_COLOUR.get(severity, "")
        tag    = "RESOLVED" if status == "resolved" else status.upper()

        logger.info(
            "%s[%s] %s (%s)%s\n  Summary: %s\n  Description: %s\n  Started: %s%s",
            colour, tag, name, severity, RESET,
            summary,
            desc[:200] + ("..." if len(desc) > 200 else ""),
            starts_at,
            f"\n  Resolved: {ends_at}" if status == "resolved" else "",
        )

    return jsonify({"received": len(alerts)}), 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    logger.info("Webhook receiver listening on :5001")
    app.run(host="0.0.0.0", port=5001)
