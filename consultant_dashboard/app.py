import argparse
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, redirect, request, url_for
from werkzeug.security import generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from .core.auth import auth_bp, configure_session, require_admin_auth_file
from .core.config import load_config
from .core.db import create_client, create_consultant, get_db, init_db, upsert_client_auth_identity
from .core.internal_api import internal_bp
from .core.realtime import configure_realtime
from .core.vendors import TenantPrefixMiddleware, current_branding, get_current_vendor, tenant_path, tenant_url_for
from .core.web import web_bp

PASSWORD_HASH_METHOD = "pbkdf2:sha256"


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _format_display_datetime(value: str, display_timezone: str = "Europe/London") -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        local_tz = ZoneInfo(display_timezone)
    except Exception:
        local_tz = timezone.utc
    return dt.astimezone(local_tz).strftime("%d %b %Y, %H:%M")


def create_app() -> Flask:
    load_dotenv()
    app = Flask(
        __name__,
        template_folder="templates",
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.wsgi_app = TenantPrefixMiddleware(app.wsgi_app)
    config = load_config()
    app.config.update(config)
    app.secret_key = config["SESSION_SECRET"]
    configure_session(app)
    configure_realtime(app)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith(("/admin", "/consultant", "/client")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    require_admin_auth_file(config["ADMIN_AUTH_FILE"])

    db_path = Path(config["DB_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage_root = Path(config["STORAGE_ROOT"])
    storage_root.mkdir(parents=True, exist_ok=True)
    init_db(config)

    app.register_blueprint(auth_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(web_bp)
    app.jinja_env.filters["display_dt"] = lambda value: _format_display_datetime(
        value, app.config.get("DISPLAY_TIMEZONE", "Europe/London")
    )

    @app.context_processor
    def inject_vendor_branding():
        vendor = get_current_vendor()
        branding = current_branding()
        return {
            "current_vendor": vendor,
            "vendor_branding": branding,
            "brand_name": branding.get("name") or app.config["BRAND_NAME"],
            "tenant_path": tenant_path,
            "tenant_url_for": tenant_url_for,
        }

    @app.route("/")
    def root():
        if request.environ.get("mindfix.vendor_slug"):
            return redirect(url_for("web.vendor_public_index"))
        return redirect(url_for("web.home"))

    @app.route("/health")
    def health():
        db = get_db(config)
        db.execute("SELECT 1")
        db.close()
        return {
            "status": "ok",
            "service": "consultant-dashboard",
        }

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve")
    sub.add_parser("init-db")

    hp = sub.add_parser("hash-password")
    hp.add_argument("--password", required=True)

    cc = sub.add_parser("create-consultant")
    cc.add_argument("--email", required=True)
    cc.add_argument("--name", required=True)
    cc.add_argument("--phone", required=True)
    cc.add_argument("--password", required=True)
    cc.add_argument("--notification-email")
    cc.add_argument("--escalation-phone-number")

    cl = sub.add_parser("create-client")
    cl.add_argument("--consultant-id", required=True)
    cl.add_argument("--name", required=True)
    cl.add_argument("--email", default="")
    cl.add_argument("--password", default="")
    cl.add_argument("--phone", default="")
    cl.add_argument("--notification-email", default="")
    cl.add_argument("--escalation-phone-number", default="")
    cl.add_argument("--notes", default="")
    cl.add_argument("--direction", default="")

    lia = sub.add_parser("link-client-auth")
    lia.add_argument("--client-id", required=True)
    lia.add_argument("--google-sub", default="")
    lia.add_argument("--email", default="")
    lia.add_argument("--name", default="")
    lia.add_argument("--phone", default="")

    args = parser.parse_args()
    load_dotenv()

    if args.cmd == "hash-password":
        print(generate_password_hash(args.password, method=PASSWORD_HASH_METHOD))
        return 0

    config = load_config()
    require_admin_auth_file(config["ADMIN_AUTH_FILE"])

    if args.cmd == "init-db":
        init_db(config)
        print(f"Initialized database at {config['DB_PATH']}")
        return 0

    if args.cmd == "create-consultant":
        init_db(config)
        db = get_db(config)
        try:
            create_consultant(
                db,
                email=args.email,
                name=args.name,
                phone_number=args.phone,
                password_hash=generate_password_hash(args.password, method=PASSWORD_HASH_METHOD),
                notification_email=args.notification_email or args.email,
                escalation_phone_number=args.escalation_phone_number or args.phone,
            )
            db.commit()
            print(f"Created consultant {args.email}")
            return 0
        except sqlite3.IntegrityError:
            db.rollback()
            print(f"Consultant already exists: {args.email}")
            return 1
        finally:
            db.close()

    if args.cmd == "create-client":
        init_db(config)
        db = get_db(config)
        client_id = create_client(
            db,
            consultant_id=args.consultant_id,
            first_name=(args.name or "").strip().split(" ", 1)[0] if (args.name or "").strip() else "",
            last_name=(args.name or "").strip().split(" ", 1)[1] if " " in (args.name or "").strip() else "",
            email=args.email,
            password_hash=generate_password_hash(args.password, method=PASSWORD_HASH_METHOD) if args.password else "",
            phone_number=args.phone,
            notification_email=args.notification_email or args.email,
            escalation_phone_number=args.escalation_phone_number or args.phone,
            year_of_birth=None,
            sex="",
            notes=args.notes,
            direction=args.direction,
        )
        db.commit()
        db.close()
        print(client_id)
        return 0

    if args.cmd == "link-client-auth":
        init_db(config)
        db = get_db(config)
        upsert_client_auth_identity(
            db,
            client_id=args.client_id,
            google_sub_hash=_hash_value(args.google_sub) if args.google_sub else "",
            email_hash=_hash_value(args.email) if args.email else "",
            normalized_name_hash=_hash_value(" ".join(args.name.split())) if args.name else "",
            phone_hash=_hash_value("".join(ch for ch in args.phone if ch.isdigit() or ch == "+")) if args.phone else "",
        )
        db.commit()
        db.close()
        print(f"Linked auth identity for {args.client_id}")
        return 0

    if args.cmd == "serve":
        init_db(config)
        app = create_app()
        app.run(
            host=config["HOST"],
            port=config["PORT"],
            debug=config["AUTH_DEV_MODE"],
        )
        return 0

    return 1
