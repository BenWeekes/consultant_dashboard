import configparser
import os
from pathlib import Path
from typing import Tuple


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_admin_auth_file(path: str) -> Tuple[str, int]:
    cp = configparser.ConfigParser(interpolation=None)
    with open(path, "r", encoding="utf-8") as f:
        raw = "[admin]\n" + f.read()
    cp.read_string(raw)
    secret = cp["admin"].get("session_secret", "").strip()
    ttl = cp["admin"].get("session_ttl", "28800").strip()
    if not secret:
        raise RuntimeError("admin auth file missing session_secret")
    try:
        ttl_int = int(ttl)
    except ValueError as exc:
        raise RuntimeError("admin auth file has invalid session_ttl") from exc
    return secret, ttl_int


def load_config() -> dict:
    admin_auth_file = _require_env("CONSULTANT_ADMIN_AUTH_FILE")
    admin_auth_file = str(Path(admin_auth_file).expanduser().resolve())
    session_secret, session_ttl = _parse_admin_auth_file(admin_auth_file)
    storage_root = _require_env("THERAPY_STORAGE_ROOT")
    storage_root = str(Path(storage_root).expanduser().resolve())
    db_path = _require_env("CONSULTANT_DB_PATH")
    db_path = str(Path(db_path).expanduser().resolve())
    return {
        "HOST": os.environ.get("CONSULTANT_DASHBOARD_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("CONSULTANT_DASHBOARD_PORT", "8090")),
        "PUBLIC_BASE_URL": os.environ.get("CONSULTANT_PUBLIC_BASE_URL", ""),
        "DB_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "STORAGE_BACKEND": os.environ.get("THERAPY_STORAGE_BACKEND", "filesystem"),
        "MASTER_KEY": _require_env("THERAPY_MASTER_KEY"),
        "INTERNAL_SHARED_SECRET": _require_env("CONSULTANT_INTERNAL_SHARED_SECRET"),
        "ADMIN_AUTH_FILE": admin_auth_file,
        "SESSION_SECRET": session_secret,
        "SESSION_TTL": int(os.environ.get("CONSULTANT_SESSION_TTL", str(session_ttl))),
        "AUTH_DEV_MODE": os.environ.get("CONSULTANT_AUTH_DEV_MODE", "").lower() == "true",
        "TWILIO_ACCOUNT_SID": os.environ.get("CONSULTANT_TWILIO_ACCOUNT_SID", ""),
        "TWILIO_AUTH_TOKEN": os.environ.get("CONSULTANT_TWILIO_AUTH_TOKEN", ""),
        "TWILIO_VERIFY_SERVICE_SID": os.environ.get("CONSULTANT_TWILIO_VERIFY_SERVICE_SID", ""),
        "TWILIO_MESSAGING_SERVICE_SID": os.environ.get("CONSULTANT_TWILIO_MESSAGING_SERVICE_SID", ""),
        "TWILIO_FROM_NUMBER": os.environ.get("CONSULTANT_TWILIO_FROM_NUMBER", ""),
        "SENDGRID_API_KEY": os.environ.get("CONSULTANT_SENDGRID_API_KEY", ""),
        "EMAIL_FROM": os.environ.get("CONSULTANT_EMAIL_FROM", ""),
        "EMAIL_REPLY_TO": os.environ.get("CONSULTANT_EMAIL_REPLY_TO", ""),
        "GOOGLE_CLIENT_ID": os.environ.get("CONSULTANT_GOOGLE_CLIENT_ID", os.environ.get("THERAPY_GOOGLE_CLIENT_ID", "")),
        "GOOGLE_CLIENT_SECRET": os.environ.get("CONSULTANT_GOOGLE_CLIENT_SECRET", os.environ.get("THERAPY_GOOGLE_CLIENT_SECRET", "")),
        "OUTBOUND_REQUEST_TIMEOUT_SECONDS": int(os.environ.get("CONSULTANT_OUTBOUND_REQUEST_TIMEOUT_SECONDS", "8")),
        "BRAND_NAME": os.environ.get("THERAPY_DASHBOARD_BRAND_NAME", "mindfix.me"),
        "DISPLAY_TIMEZONE": os.environ.get("CONSULTANT_DISPLAY_TIMEZONE", "Europe/London"),
        "CLIENT_APP_URL": os.environ.get("THERAPY_CLIENT_APP_URL", "http://localhost:8084"),
        "CLIENT_PROFILE": os.environ.get("THERAPY_CLIENT_PROFILE", "therapy"),
    }
