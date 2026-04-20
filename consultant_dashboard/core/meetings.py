import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


CHANNEL_ALPHABET = string.ascii_uppercase + string.digits


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_meeting_channel(length: int = 10) -> str:
    return "".join(secrets.choice(CHANNEL_ALPHABET) for _ in range(length))


def get_pair_channel(consultant_id: str, client_id: str, meeting_type: str = "human") -> str:
    normalized_type = (meeting_type or "human").strip().lower()
    digest = hashlib.sha256(f"{normalized_type}:{consultant_id}:{client_id}".encode("utf-8")).hexdigest()[:16]
    return f"room_{digest}"


def build_join_window(start_at: datetime, end_at: datetime) -> Tuple[datetime, datetime]:
    return start_at - timedelta(minutes=15), end_at + timedelta(minutes=30)


def make_signed_join_bootstrap(secret: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def _verify_signed_payload(secret: str, token: str) -> Optional[dict]:
    try:
        encoded, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    exp = int(payload.get("exp", 0))
    if exp and exp < int(time.time()):
        return None
    return payload


def verify_signed_join_bootstrap(secret: str, token: str) -> Optional[dict]:
    return _verify_signed_payload(secret, token)


def make_signed_meeting_access_token(secret: str, response_access_link_id: str, exp: int) -> str:
    return make_signed_join_bootstrap(
        secret,
        {
            "kind": "meeting_access",
            "response_access_link_id": response_access_link_id,
            "exp": exp,
        },
    )


def verify_signed_meeting_access_token(secret: str, token: str) -> Optional[dict]:
    payload = _verify_signed_payload(secret, token)
    if not payload or payload.get("kind") != "meeting_access":
        return None
    return payload
