import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple
from urllib import error, parse, request


def build_public_url(config: dict, path: str) -> str:
    base = config.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        host = config.get("HOST", "127.0.0.1")
        port = config.get("PORT", 8090)
        base = f"http://{host}:{port}"
    return f"{base}{path}"


def new_access_token() -> str:
    return secrets.token_urlsafe(24)


def hash_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def default_expiry(hours: int = 24 * 14) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def build_reply_link(config: dict, token: str) -> str:
    return build_public_url(config, f"/client/messages/{token}")


def is_sendgrid_enabled(config: dict) -> bool:
    return bool(config.get("SENDGRID_API_KEY") and config.get("EMAIL_FROM"))


def is_twilio_messaging_enabled(config: dict) -> bool:
    return bool(
        config.get("TWILIO_ACCOUNT_SID")
        and config.get("TWILIO_AUTH_TOKEN")
        and (
            config.get("TWILIO_MESSAGING_SERVICE_SID")
            or config.get("TWILIO_FROM_NUMBER")
        )
    )


def deliver_email(config: dict, *, to_email: str, subject: str, body: str, reply_link: str, kind: str) -> Tuple[str, str]:
    if not to_email:
        return "not_sent", "client_email_missing"
    if not is_sendgrid_enabled(config):
        return "not_sent", "sendgrid_not_configured"

    plain_text = (
        f"{body}\n\n"
        f"This secure link is for the client to read and reply: {reply_link}\n"
        f"Consultants should continue the conversation from the {config['BRAND_NAME']} dashboard.\n\n"
        f"Sent by {config['BRAND_NAME']}."
    )
    html_body = (
        f"<p>{body.replace(chr(10), '<br>')}</p>"
        f"<p><strong>This secure link is for the client to read and reply.</strong></p>"
        f"<p><a href=\"{reply_link}\">Open secure client reply</a></p>"
        f"<p>Consultants should continue the conversation from the {config['BRAND_NAME']} dashboard.</p>"
        f"<p>Sent by {config['BRAND_NAME']}.</p>"
    )
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": config["EMAIL_FROM"]},
        "subject": subject or f"{config['BRAND_NAME']} message",
        "content": [
            {"type": "text/plain", "value": plain_text},
            {"type": "text/html", "value": html_body},
        ],
        "custom_args": {"kind": kind},
    }
    if config.get("EMAIL_REPLY_TO"):
        payload["reply_to"] = {"email": config["EMAIL_REPLY_TO"]}
    req = request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['SENDGRID_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=config.get("OUTBOUND_REQUEST_TIMEOUT_SECONDS", 8)) as resp:
            status = getattr(resp, "status", 202)
        if 200 <= status < 300:
            return "sent", ""
        return "failed", f"sendgrid_status_{status}"
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        return "failed", f"sendgrid_http_{exc.code}:{detail}"
    except Exception as exc:  # pragma: no cover - network failure path
        return "failed", str(exc)


def deliver_sms(config: dict, *, to_phone: str, body: str, reply_link: str) -> Tuple[str, str]:
    if not to_phone:
        return "not_sent", "client_phone_missing"
    if not is_twilio_messaging_enabled(config):
        return "not_sent", "twilio_messaging_not_configured"

    message_body = f"{body.strip()}\nReply securely: {reply_link}"
    form = {
        "To": to_phone,
        "Body": message_body[:1500],
    }
    if config.get("TWILIO_MESSAGING_SERVICE_SID"):
        form["MessagingServiceSid"] = config["TWILIO_MESSAGING_SERVICE_SID"]
    else:
        form["From"] = config["TWILIO_FROM_NUMBER"]

    url = f"https://api.twilio.com/2010-04-01/Accounts/{config['TWILIO_ACCOUNT_SID']}/Messages.json"
    basic = base64.b64encode(
        f"{config['TWILIO_ACCOUNT_SID']}:{config['TWILIO_AUTH_TOKEN']}".encode("utf-8")
    ).decode("ascii")
    req = request.Request(
        url,
        data=parse.urlencode(form).encode("utf-8"),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=config.get("OUTBOUND_REQUEST_TIMEOUT_SECONDS", 8)) as resp:
            response_body = resp.read().decode("utf-8", "ignore")
            status = getattr(resp, "status", 201)
        if 200 <= status < 300:
            return "sent", response_body[:200]
        return "failed", f"twilio_status_{status}"
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        return "failed", f"twilio_http_{exc.code}:{detail}"
    except Exception as exc:  # pragma: no cover - network failure path
        return "failed", str(exc)


def choose_delivery_channel(*, client_email: str, client_phone: str) -> str:
    if client_email:
        return "email"
    if client_phone:
        return "sms"
    return "portal"


def build_delivery_content(*, channel: str, brand: str, client_name: str, body: str, meeting_link: str = "") -> Tuple[str, str]:
    subject_map = {
        "email": f"{brand} message",
        "sms": "",
        "meeting_invite": f"{brand} meeting invite",
    }
    message = body.strip()
    if channel == "meeting_invite" and meeting_link:
        message = f"{message}\n\nMeeting link: {meeting_link}"
    if not message:
        if channel == "meeting_invite":
            message = f"Hello {client_name}, your consultant has shared a meeting invite."
        else:
            message = f"Hello {client_name}, you have a new message from {brand}."
    return subject_map.get(channel, f"{brand} message"), message
