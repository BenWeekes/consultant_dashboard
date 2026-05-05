import configparser
import base64
import hashlib
import hmac
import json
import os
import random
import time
import urllib.parse
import urllib.request
from base64 import urlsafe_b64decode
from datetime import timedelta
from functools import wraps
from typing import Dict, Optional, Tuple

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_consultant_by_email, get_consultant_by_id, get_db, get_vendor_by_slug, log_audit, update_consultant_password
from .vendors import current_branding, current_vendor_slug, tenant_url_for

auth_bp = Blueprint("auth", __name__)


def _brand_name() -> str:
    return current_branding().get("name") or current_app.config["BRAND_NAME"]


def _current_vendor_id() -> str:
    db = get_db(current_app.config)
    vendor = get_vendor_by_slug(db, current_vendor_slug())
    db.close()
    return vendor["id"] if vendor else ""


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


def _write_admin_users(path: str, users: Dict[str, str], secret: str, ttl: int) -> None:
    lines = [
        f"session_secret={secret}",
        f"session_ttl={ttl}",
    ]
    for email in sorted(users.keys()):
        lines.append(f"{email}={users[email]}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def _send_or_store_code(phone_number: str, code: str) -> None:
    cfg = current_app.config
    if (
        cfg["TWILIO_ACCOUNT_SID"]
        and cfg["TWILIO_AUTH_TOKEN"]
        and cfg["TWILIO_VERIFY_SERVICE_SID"]
        and not cfg["AUTH_DEV_MODE"]
    ):
        print(
            f"[consultant-dashboard] Sending Twilio Verify OTP to {phone_number} "
            f"service={cfg['TWILIO_VERIFY_SERVICE_SID'][:6]}..."
        )
        from twilio.rest import Client
        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        result = client.verify.v2.services(cfg["TWILIO_VERIFY_SERVICE_SID"]).verifications.create(
            to=phone_number, channel="sms"
        )
        print(
            f"[consultant-dashboard] Twilio Verify send status={getattr(result, 'status', 'unknown')} "
            f"to={phone_number}"
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
        from twilio.base.exceptions import TwilioRestException

        cfg = current_app.config
        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        try:
            result = client.verify.v2.services(cfg["TWILIO_VERIFY_SERVICE_SID"]).verification_checks.create(
                to=phone_number,
                code=code,
            )
        except TwilioRestException as exc:
            print(
                f"[consultant-dashboard] Twilio Verify check failed for {phone_number}: "
                f"status={getattr(exc, 'status', 'unknown')} code={getattr(exc, 'code', 'unknown')} msg={exc}"
            )
            return False
        print(
            f"[consultant-dashboard] Twilio Verify check status={result.status} "
            f"to={phone_number}"
        )
        return result.status == "approved"
    return (
        int(time.time()) <= session.get("pending_code_exp", 0)
        and code == session.get("pending_code")
    )


def _clear_consultant_pending_session() -> None:
    for key in ("pending_role", "pending_consultant_id", "pending_phone", "pending_code", "pending_code_exp"):
        session.pop(key, None)


def _google_oauth_enabled() -> bool:
    return bool(current_app.config.get("GOOGLE_CLIENT_ID"))


def _shared_google_callback_url() -> str:
    base = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        base = request.url_root.rstrip("/")
    return f"{base}/auth/google/callback"


def _sign_dashboard_handoff(payload: Dict) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        current_app.config["INTERNAL_SHARED_SECRET"].encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{signature}"


def _peek_dashboard_handoff(token: str) -> Optional[Dict]:
    try:
        body, _sig = token.split(".", 1)
        padded = body + "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None


def _verify_dashboard_handoff(token: str, *, purpose: str) -> Optional[Dict]:
    try:
        body, supplied_signature = token.split(".", 1)
    except ValueError:
        return None
    expected_signature = hmac.new(
        current_app.config["INTERNAL_SHARED_SECRET"].encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, supplied_signature):
        return None
    payload = _peek_dashboard_handoff(token)
    if not payload or payload.get("purpose") != purpose:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


def _consultant_google_state() -> str:
    complete_url = f"{request.host_url.rstrip('/')}{tenant_url_for('auth.consultant_google_callback')}"
    return _sign_dashboard_handoff(
        {
            "purpose": "consultant_google_state",
            "vendor_slug": current_vendor_slug(),
            "complete_url": complete_url,
            "profile": "therapy",
            "exp": int(time.time()) + 600,
        }
    )


def _decode_google_id_token(id_token: str) -> Dict[str, str]:
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))


def _clear_consultant_session() -> None:
    _clear_consultant_pending_session()
    session.pop("consultant_id", None)


def _clear_admin_session() -> None:
    session.pop("admin_email", None)


def _require_consultant(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        consultant_id = session.get("consultant_id")
        if not consultant_id:
            return redirect(tenant_url_for("auth.consultant_login"))
        vendor_slug = current_vendor_slug()
        if vendor_slug:
            db = get_db(current_app.config)
            vendor = get_vendor_by_slug(db, vendor_slug)
            consultant = get_consultant_by_id(db, consultant_id, vendor["id"] if vendor else "")
            db.close()
            if not consultant:
                _clear_consultant_session()
                return redirect(tenant_url_for("auth.consultant_login"))
        return fn(*args, **kwargs)
    return wrapped


def _require_admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin_email"):
            return redirect(tenant_url_for("auth.admin_login"))
        return fn(*args, **kwargs)
    return wrapped


def _is_local_support_request() -> bool:
    original = request.environ.get("werkzeug.proxy_fix.orig") or {}
    peer = original.get("REMOTE_ADDR") or request.environ.get("REMOTE_ADDR", "")
    return peer.strip() in {"127.0.0.1", "::1"}


def _support_login_enabled() -> bool:
    return bool(
        current_app.config.get("LOCAL_SUPPORT_LOGIN_ENABLED")
        and current_app.config.get("LOCAL_SUPPORT_LOGIN_SECRET", "").strip()
    )


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
        return render_template(
            "consultant/login.html",
            brand=_brand_name(),
            theme="consultant",
            show_google_login=_google_oauth_enabled() or current_app.config["AUTH_DEV_MODE"],
        )

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    db = get_db(current_app.config)
    consultant = get_consultant_by_email(db, email, vendor_id=_current_vendor_id())
    if not consultant or not check_password_hash(consultant["password_hash"], password):
        _record_audit("consultant", email or "unknown", "login_failed")
        flash("Invalid email or password", "error")
        db.close()
        return render_template(
            "consultant/login.html",
            brand=_brand_name(),
            theme="consultant",
            show_google_login=_google_oauth_enabled() or current_app.config["AUTH_DEV_MODE"],
        ), 401

    code = "000000" if current_app.config["AUTH_DEV_MODE"] else f"{random.randint(0, 999999):06d}"
    _clear_consultant_pending_session()
    session["pending_role"] = "consultant"
    session["pending_consultant_id"] = consultant["id"]
    session["pending_phone"] = consultant["phone_number"]
    session["pending_code"] = code
    session["pending_code_exp"] = int(time.time()) + 300
    try:
        _send_or_store_code(consultant["phone_number"], code)
    except Exception as exc:
        print(f"[consultant-dashboard] Failed to send OTP to {consultant['phone_number']}: {exc}")
        flash("Failed to send verification code. Please check the phone number and Twilio setup.", "error")
        db.close()
        return render_template(
            "consultant/login.html",
            brand=_brand_name(),
            theme="consultant",
            show_google_login=_google_oauth_enabled() or current_app.config["AUTH_DEV_MODE"],
        ), 500
    _record_audit("consultant", consultant["id"], "login_password_verified")
    db.close()
    return redirect(tenant_url_for("auth.consultant_verify"))


@auth_bp.route("/consultant/google", methods=["GET"])
def consultant_google():
    if current_app.config["AUTH_DEV_MODE"]:
        session["consultant_google_email"] = "consultant@example.com"
        return redirect(tenant_url_for("auth.consultant_google_callback", code="dev-mode"))

    client_id = current_app.config.get("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        flash("Google sign-in is not configured for consultants.", "error")
        return redirect(tenant_url_for("auth.consultant_login"))

    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _shared_google_callback_url(),
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "select_account",
            "state": _consultant_google_state(),
        }
    )
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@auth_bp.route("/consultant/google/callback", methods=["GET"])
def consultant_google_callback():
    consultant_token = request.args.get("consultant_token", "").strip()
    if consultant_token:
        handoff = _verify_dashboard_handoff(consultant_token, purpose="consultant_google_complete")
        if not handoff:
            flash("Google authentication failed.", "error")
            return redirect(tenant_url_for("auth.consultant_login"))
        if handoff.get("vendor_slug") and handoff.get("vendor_slug") != current_vendor_slug():
            flash("Google authentication failed.", "error")
            return redirect(tenant_url_for("auth.consultant_login"))
        email = (handoff.get("email") or "").strip().lower()
    elif current_app.config["AUTH_DEV_MODE"] and request.args.get("code") == "dev-mode":
        email = session.get("consultant_google_email", "consultant@example.com").strip().lower()
    else:
        code = request.args.get("code", "").strip()
        if not code:
            flash("Missing Google authorization code.", "error")
            return redirect(tenant_url_for("auth.consultant_login"))

        token_data = urllib.parse.urlencode(
            {
                "code": code,
                "client_id": current_app.config.get("GOOGLE_CLIENT_ID", ""),
                "client_secret": current_app.config.get("GOOGLE_CLIENT_SECRET", ""),
                "redirect_uri": _shared_google_callback_url(),
                "grant_type": "authorization_code",
            }
        ).encode("utf-8")
        try:
            token_req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(token_req, timeout=10) as resp:
                token_resp = json.loads(resp.read().decode("utf-8"))
            email = (_decode_google_id_token(token_resp.get("id_token", "")).get("email") or "").strip().lower()
        except Exception as exc:
            print(f"[consultant-dashboard] Google consultant token exchange failed: {exc}")
            flash("Google authentication failed.", "error")
            return redirect(tenant_url_for("auth.consultant_login"))

    if not email:
        flash("Google account did not return an email address.", "error")
        return redirect(tenant_url_for("auth.consultant_login"))

    db = get_db(current_app.config)
    consultant = get_consultant_by_email(db, email, vendor_id=_current_vendor_id())
    if not consultant:
        db.close()
        _record_audit("consultant", email, "google_login_failed")
        flash("No consultant account exists for that Google email on this vendor.", "error")
        return redirect(tenant_url_for("auth.consultant_login"))

    code = "000000" if current_app.config["AUTH_DEV_MODE"] else f"{random.randint(0, 999999):06d}"
    _clear_consultant_pending_session()
    session["pending_role"] = "consultant"
    session["pending_consultant_id"] = consultant["id"]
    session["pending_phone"] = consultant["phone_number"]
    session["pending_code"] = code
    session["pending_code_exp"] = int(time.time()) + 300
    try:
        _send_or_store_code(consultant["phone_number"], code)
    except Exception as exc:
        print(f"[consultant-dashboard] Failed to send Google OTP to {consultant['phone_number']}: {exc}")
        flash("Failed to send verification code.", "error")
        db.close()
        return redirect(tenant_url_for("auth.consultant_login"))
    _record_audit("consultant", consultant["id"], "login_google_verified", {"email": email})
    db.close()
    return redirect(tenant_url_for("auth.consultant_verify"))


@auth_bp.route("/consultant/verify", methods=["GET", "POST"])
def consultant_verify():
    if session.get("pending_role") != "consultant":
        return redirect(tenant_url_for("auth.consultant_login"))
    if request.method == "GET":
        return render_template("consultant/verify.html", brand=_brand_name(), theme="consultant")

    code = request.form.get("code", "").strip()
    if not _verify_code(session.get("pending_phone", ""), code):
        flash("Invalid or expired code", "error")
        _record_audit("consultant", session.get("pending_consultant_id", "unknown"), "login_otp_failed")
        return render_template("consultant/verify.html", brand=_brand_name(), theme="consultant"), 401

    consultant_id = session["pending_consultant_id"]
    _clear_consultant_pending_session()
    session["consultant_id"] = consultant_id
    session.permanent = True
    _record_audit("consultant", consultant_id, "login_success")
    return redirect(tenant_url_for("web.consultant_dashboard"))


@auth_bp.route("/consultant/local-support-login", methods=["GET", "POST"])
def consultant_local_support_login():
    if not _is_local_support_request() or not _support_login_enabled():
        return ("Not found", 404)

    if request.method == "GET":
        return render_template(
            "consultant/local_support_login.html",
            brand=_brand_name(),
            theme="consultant",
        )

    secret = request.form.get("secret", "").strip()
    email = request.form.get("email", "").strip().lower()
    expected_secret = current_app.config.get("LOCAL_SUPPORT_LOGIN_SECRET", "").strip()
    if not secret or not expected_secret or not hmac.compare_digest(secret, expected_secret):
        _record_audit("consultant", email or "unknown", "local_support_login_failed")
        flash("Invalid support secret", "error")
        return render_template(
            "consultant/local_support_login.html",
            brand=_brand_name(),
            theme="consultant",
        ), 403

    db = get_db(current_app.config)
    consultant = get_consultant_by_email(db, email, vendor_id=_current_vendor_id())
    db.close()
    if not consultant:
        _record_audit("consultant", email or "unknown", "local_support_login_failed")
        flash("Consultant account not found for this vendor", "error")
        return render_template(
            "consultant/local_support_login.html",
            brand=_brand_name(),
            theme="consultant",
        ), 404

    _clear_consultant_session()
    session["consultant_id"] = consultant["id"]
    session.permanent = True
    _record_audit(
        "consultant",
        consultant["id"],
        "local_support_login_success",
        {"email": consultant["email"], "remote_addr": request.remote_addr or ""},
    )
    return redirect(tenant_url_for("web.consultant_dashboard"))


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin/login.html", brand=_brand_name())

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    users, _secret, _ttl = _load_admin_users(current_app.config["ADMIN_AUTH_FILE"])
    hashed = users.get(email)
    if not hashed or not check_password_hash(hashed, password):
        _record_audit("admin", email or "unknown", "login_failed")
        flash("Invalid email or password", "error")
        return render_template("admin/login.html", brand=_brand_name()), 401
    session["admin_email"] = email
    session.permanent = True
    _record_audit("admin", email, "login_success")
    return redirect(tenant_url_for("web.admin_dashboard"))


@auth_bp.route("/consultant/account", methods=["GET", "POST"])
@_require_consultant
def consultant_account():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    consultant = get_consultant_by_id(db, consultant_id)
    if not consultant:
        db.close()
        _clear_consultant_session()
        return redirect(tenant_url_for("auth.consultant_login"))

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not check_password_hash(consultant["password_hash"], current_password):
            flash("Current password is incorrect", "error")
        elif not new_password or len(new_password) < 8:
            flash("New password must be at least 8 characters", "error")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match", "error")
        else:
            update_consultant_password(
                db,
                consultant_id=consultant_id,
                password_hash=generate_password_hash(new_password, method="pbkdf2:sha256"),
            )
            db.commit()
            _record_audit("consultant", consultant_id, "password_changed")
            flash("Password updated", "muted")
            consultant = get_consultant_by_id(db, consultant_id)
    db.close()
    return render_template(
        "consultant/account.html",
        brand=_brand_name(),
        theme="consultant",
        consultant=consultant,
    )


@auth_bp.route("/admin/account", methods=["GET", "POST"])
@_require_admin
def admin_account():
    admin_email = session.get("admin_email", "")
    users, secret, ttl = _load_admin_users(current_app.config["ADMIN_AUTH_FILE"])
    current_hash = users.get(admin_email)
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not current_hash or not check_password_hash(current_hash, current_password):
            flash("Current password is incorrect", "error")
        elif not new_password or len(new_password) < 8:
            flash("New password must be at least 8 characters", "error")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match", "error")
        else:
            users[admin_email] = generate_password_hash(new_password, method="pbkdf2:sha256")
            _write_admin_users(current_app.config["ADMIN_AUTH_FILE"], users, secret, ttl)
            _record_audit("admin", admin_email, "password_changed")
            flash("Password updated", "muted")
    return render_template(
        "admin/account.html",
        brand=_brand_name(),
        admin_email=admin_email,
    )


@auth_bp.route("/consultant/logout", methods=["POST"])
def consultant_logout():
    consultant_id = session.get("consultant_id") or "unknown"
    _clear_consultant_session()
    _record_audit("consultant", str(consultant_id), "logout")
    return redirect(tenant_url_for("auth.consultant_login"))


@auth_bp.route("/admin/logout", methods=["POST"])
def admin_logout():
    admin_email = session.get("admin_email") or "unknown"
    _clear_admin_session()
    _record_audit("admin", str(admin_email), "logout")
    return redirect(tenant_url_for("auth.admin_login"))


@auth_bp.route("/logout", methods=["POST"])
def logout():
    consultant_id = session.get("consultant_id")
    admin_email = session.get("admin_email")
    actor_id = consultant_id or admin_email or "unknown"
    _clear_consultant_session()
    _clear_admin_session()
    _record_audit("unknown", str(actor_id), "logout")
    if admin_email and not consultant_id:
        return redirect(tenant_url_for("auth.admin_login"))
    return redirect(tenant_url_for("auth.consultant_login"))


def require_consultant(fn):
    return _require_consultant(fn)


def require_admin(fn):
    return _require_admin(fn)


def configure_session(app) -> None:
    app.permanent_session_lifetime = timedelta(seconds=app.config["SESSION_TTL"])
