import configparser
import os
import random
import time
from datetime import timedelta
from functools import wraps
from typing import Dict, Optional, Tuple

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .db import get_consultant_by_email, get_db, log_audit

auth_bp = Blueprint("auth", __name__)


def require_admin_auth_file(path: str) -> None:
    if not os.path.exists(path):
        raise RuntimeError(f"Admin auth file not found: {path}")
    if os.path.isdir(path):
        raise RuntimeError(f"Admin auth path is a directory: {path}")
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError(f"Admin auth file permissions must be 600 or stricter: {path}")


def _load_admin_users(path: str) -> Tuple[Dict[str, str], str, int]:
    cp = configparser.ConfigParser(interpolation=None)
    with open(path, "r", encoding="utf-8") as f:
        raw = "[admin]\n" + f.read()
    cp.read_string(raw)
    secret = cp["admin"].get("session_secret", "").strip()
    ttl = int(cp["admin"].get("session_ttl", "28800"))
    users = {
        key.lower(): value.strip()
        for key, value in cp["admin"].items()
        if key not in {"session_secret", "session_ttl"}
    }
    if not secret or not users:
        raise RuntimeError("Admin auth file must contain session_secret and at least one admin user")
    return users, secret, ttl


def _send_or_store_code(phone_number: str, code: str) -> None:
    cfg = current_app.config
    if (
        cfg["TWILIO_ACCOUNT_SID"]
        and cfg["TWILIO_AUTH_TOKEN"]
        and cfg["TWILIO_VERIFY_SERVICE_SID"]
        and not cfg["AUTH_DEV_MODE"]
    ):
        from twilio.rest import Client
        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        client.verify.v2.services(cfg["TWILIO_VERIFY_SERVICE_SID"]).verifications.create(
            to=phone_number, channel="sms"
        )
        return
    print(f"[consultant-dashboard] OTP for {phone_number}: {code}")


def _is_twilio_verify_enabled() -> bool:
    cfg = current_app.config
    return bool(
        cfg["TWILIO_ACCOUNT_SID"]
        and cfg["TWILIO_AUTH_TOKEN"]
        and cfg["TWILIO_VERIFY_SERVICE_SID"]
        and not cfg["AUTH_DEV_MODE"]
    )


def _verify_code(phone_number: str, code: str) -> bool:
    if _is_twilio_verify_enabled():
        from twilio.rest import Client

        cfg = current_app.config
        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        result = client.verify.v2.services(cfg["TWILIO_VERIFY_SERVICE_SID"]).verification_checks.create(
            to=phone_number,
            code=code,
        )
        return result.status == "approved"
    return (
        int(time.time()) <= session.get("pending_code_exp", 0)
        and code == session.get("pending_code")
    )


def _require_role(role: str):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for(f"auth.{role}_login"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def _record_audit(actor_type: str, actor_id: str, action: str, details: Optional[Dict] = None) -> None:
    db = get_db(current_app.config)
    log_audit(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
        details=details,
    )
    db.commit()
    db.close()


@auth_bp.route("/consultant/login", methods=["GET", "POST"])
def consultant_login():
    if request.method == "GET":
        return render_template("consultant/login.html", brand=current_app.config["BRAND_NAME"])

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    db = get_db(current_app.config)
    consultant = get_consultant_by_email(db, email)
    if not consultant or not check_password_hash(consultant["password_hash"], password):
        _record_audit("consultant", email or "unknown", "login_failed")
        flash("Invalid email or password", "error")
        db.close()
        return render_template("consultant/login.html", brand=current_app.config["BRAND_NAME"]), 401

    code = "000000" if current_app.config["AUTH_DEV_MODE"] else f"{random.randint(0, 999999):06d}"
    session.clear()
    session["pending_role"] = "consultant"
    session["pending_consultant_id"] = consultant["id"]
    session["pending_phone"] = consultant["phone_number"]
    session["pending_code"] = code
    session["pending_code_exp"] = int(time.time()) + 300
    _send_or_store_code(consultant["phone_number"], code)
    _record_audit("consultant", consultant["id"], "login_password_verified")
    db.close()
    return redirect(url_for("auth.consultant_verify"))


@auth_bp.route("/consultant/verify", methods=["GET", "POST"])
def consultant_verify():
    if session.get("pending_role") != "consultant":
        return redirect(url_for("auth.consultant_login"))
    if request.method == "GET":
        return render_template("consultant/verify.html", brand=current_app.config["BRAND_NAME"])

    code = request.form.get("code", "").strip()
    if not _verify_code(session.get("pending_phone", ""), code):
        flash("Invalid or expired code", "error")
        _record_audit("consultant", session.get("pending_consultant_id", "unknown"), "login_otp_failed")
        return render_template("consultant/verify.html", brand=current_app.config["BRAND_NAME"]), 401

    consultant_id = session["pending_consultant_id"]
    session.clear()
    session["role"] = "consultant"
    session["consultant_id"] = consultant_id
    session.permanent = True
    _record_audit("consultant", consultant_id, "login_success")
    return redirect(url_for("web.consultant_dashboard"))


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin/login.html", brand=current_app.config["BRAND_NAME"])

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    users, _secret, _ttl = _load_admin_users(current_app.config["ADMIN_AUTH_FILE"])
    hashed = users.get(email)
    if not hashed or not check_password_hash(hashed, password):
        _record_audit("admin", email or "unknown", "login_failed")
        flash("Invalid email or password", "error")
        return render_template("admin/login.html", brand=current_app.config["BRAND_NAME"]), 401
    session.clear()
    session["role"] = "admin"
    session["admin_email"] = email
    session.permanent = True
    _record_audit("admin", email, "login_success")
    return redirect(url_for("web.admin_dashboard"))


@auth_bp.route("/logout", methods=["POST"])
def logout():
    actor_type = session.get("role", "unknown")
    actor_id = session.get("consultant_id") or session.get("admin_email") or "unknown"
    session.clear()
    _record_audit(actor_type, str(actor_id), "logout")
    return redirect(url_for("web.home"))


def require_consultant(fn):
    return _require_role("consultant")(fn)


def require_admin(fn):
    return _require_role("admin")(fn)


def configure_session(app) -> None:
    app.permanent_session_lifetime = timedelta(seconds=app.config["SESSION_TTL"])
