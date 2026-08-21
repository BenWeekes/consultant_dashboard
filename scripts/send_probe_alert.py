#!/usr/bin/env python3
"""Send a daily probe failure alert to dashboard admins."""

from __future__ import annotations

import configparser
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from consultant_dashboard.core.config import load_config
from consultant_dashboard.core.messaging import deliver_email


def _load_admin_emails(path: str) -> list[str]:
    cp = configparser.ConfigParser(interpolation=None)
    with open(path, "r", encoding="utf-8") as handle:
        cp.read_string("[admin]\n" + handle.read())
    return [
        key
        for key in cp["admin"].keys()
        if key not in {"session_secret", "session_ttl"}
    ]


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: send_probe_alert.py <probe_json_path>"}))
        return 1

    load_dotenv(ROOT_DIR / ".env")
    config = load_config()
    payload_path = Path(sys.argv[1]).resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    recipients = _load_admin_emails(config["ADMIN_AUTH_FILE"])

    subject = f"Mindfix daily probe FAILED: {payload.get('profile', 'unknown')} at {payload.get('timestamp_utc', 'unknown')}"
    plain_text = (
        "Mindfix daily probe failure detected.\n\n"
        f"Timestamp (UTC): {payload.get('timestamp_utc', '')}\n"
        f"Profile: {payload.get('profile', '')}\n"
        f"RTM ok: {payload.get('rtm_ok')}\n"
        f"Voice ok: {payload.get('voice_ok')}\n"
        f"Log file: {payload_path}\n\n"
        "RTM output:\n"
        f"{payload.get('rtm_output', '')}\n\n"
        "Voice output:\n"
        f"{payload.get('voice_output', '')}\n"
    )
    html_body = (
        "<p>Mindfix daily probe failure detected.</p>"
        f"<p><strong>Timestamp (UTC):</strong> {payload.get('timestamp_utc', '')}<br>"
        f"<strong>Profile:</strong> {payload.get('profile', '')}<br>"
        f"<strong>RTM ok:</strong> {payload.get('rtm_ok')}<br>"
        f"<strong>Voice ok:</strong> {payload.get('voice_ok')}<br>"
        f"<strong>Log file:</strong> {payload_path}</p>"
        f"<p><strong>RTM output</strong><br><pre>{payload.get('rtm_output', '')}</pre></p>"
        f"<p><strong>Voice output</strong><br><pre>{payload.get('voice_output', '')}</pre></p>"
    )

    results = []
    overall_ok = True
    for recipient in recipients:
        status, error = deliver_email(
            config,
            to_email=recipient,
            subject=subject,
            body="Mindfix daily probe failure detected.",
            reply_link="https://mindfix.me/admin",
            kind="daily_probe_failure",
            plain_text_override=plain_text,
            html_override=html_body,
        )
        item = {"to": recipient, "status": status, "error": error}
        results.append(item)
        if status != "sent":
            overall_ok = False

    print(json.dumps({"ok": overall_ok, "results": results}))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
