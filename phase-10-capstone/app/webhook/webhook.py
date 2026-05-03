import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# RECEIVER_NAME identifies which Alertmanager receiver sent to this instance.
# When running two webhooks (webhook-dev and webhook-prod) the name makes it
# immediately clear which environment an alert belongs to.
RECEIVER_NAME = os.getenv("RECEIVER_NAME", "default")

SEVERITY_COLOURS = {
    "critical": "\033[91m",   # bright red
    "warning":  "\033[93m",   # bright yellow
    "none":     "\033[94m",   # bright blue
}
RESET  = "\033[0m"
GREEN  = "\033[92m"
BOLD   = "\033[1m"

ENV_COLOURS = {
    "prod": "\033[91m",   # red — prod always stands out
    "dev":  "\033[94m",   # blue — dev is informational
    "staging": "\033[93m", # yellow
}


def _fmt_alert(alert: dict) -> str:
    labels      = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    status      = alert.get("status", "firing")
    severity    = labels.get("severity", "unknown")
    env         = labels.get("env", "unknown")

    if status == "resolved":
        colour = GREEN
        prefix = "✔ RESOLVED"
    else:
        colour = SEVERITY_COLOURS.get(severity, "\033[97m")
        prefix = f"▲ FIRING [{severity.upper()}]"

    env_colour = ENV_COLOURS.get(env, "\033[97m")
    env_tag    = f"{env_colour}[{env.upper()}]{RESET}"

    lines = [
        f"{BOLD}{colour}{prefix}{RESET} {env_tag} {labels.get('alertname', '?')}",
        f"  summary:     {annotations.get('summary', '-')}",
        f"  description: {annotations.get('description', '-').strip()}",
        f"  labels:      {json.dumps(labels, sort_keys=True)}",
        f"  starts_at:   {alert.get('startsAt', '-')}",
    ]
    if status == "resolved":
        lines.append(f"  ends_at:     {alert.get('endsAt', '-')}")
    return "\n".join(lines)


@app.route("/webhook", methods=["POST"])
def webhook():
    data    = request.get_json(force=True, silent=True) or {}
    alerts  = data.get("alerts", [])
    now     = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n{'='*70}")
    print(f"  {BOLD}{RECEIVER_NAME}{RESET}  |  {now}  |  {len(alerts)} alert(s)")
    print(f"{'='*70}")
    for alert in alerts:
        print(_fmt_alert(alert))
        print()

    return jsonify({"status": "ok", "receiver": RECEIVER_NAME}), 200


@app.route("/health")
def health():
    return jsonify({"status": "ok", "receiver": RECEIVER_NAME})


if __name__ == "__main__":
    print(f"{BOLD}Lumio webhook receiver — {RECEIVER_NAME}{RESET}")
    app.run(host="0.0.0.0", port=5001)
