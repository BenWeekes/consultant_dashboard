import sqlite3

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from .auth import require_admin, require_consultant
from .db import (
    create_client,
    create_consultant,
    get_client_detail,
    get_consultant_by_id,
    get_db,
    get_session_detail,
    list_clients_for_consultant,
    list_consultants,
    list_sessions,
    list_sessions_for_client,
    log_audit,
)
from .storage import EncryptedStorage

web_bp = Blueprint("web", __name__)


def _storage() -> EncryptedStorage:
    return EncryptedStorage(current_app.config["STORAGE_ROOT"], current_app.config["MASTER_KEY"])


def _consultant_dashboard_stats(db, consultant_id: str):
    return {
        "clients": db.execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM clients c
            JOIN consultant_clients cc ON cc.client_id = c.id
            WHERE cc.consultant_id = ? AND c.is_active = 1
            """,
            (consultant_id,),
        ).fetchone()["c"],
        "sessions": db.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE consultant_id = ?",
            (consultant_id,),
        ).fetchone()["c"],
        "alerts": db.execute(
            """
            SELECT COUNT(*) AS c
            FROM session_alerts sa
            JOIN consultant_clients cc ON cc.client_id = sa.client_id
            WHERE cc.consultant_id = ? AND sa.acknowledged_at IS NULL
            """,
            (consultant_id,),
        ).fetchone()["c"],
    }


@web_bp.get("/home")
def home():
    return render_template("shared/home.html", brand=current_app.config["BRAND_NAME"])


@web_bp.get("/consultant/dashboard")
@require_consultant
def consultant_dashboard():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    consultant = get_consultant_by_id(db, consultant_id)
    stats = _consultant_dashboard_stats(db, consultant_id)
    recent_clients = list_clients_for_consultant(db, consultant_id)[:5]
    recent_sessions = list_sessions(db, consultant_id=consultant_id, limit=5)
    db.close()
    return render_template(
        "consultant/dashboard.html",
        brand=current_app.config["BRAND_NAME"],
        stats=stats,
        consultant=consultant,
        recent_clients=recent_clients,
        recent_sessions=recent_sessions,
    )


@web_bp.route("/consultant/clients", methods=["GET", "POST"])
@require_consultant
def consultant_clients():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Client name is required", "error")
        else:
            email = request.form.get("email", "").strip()
            phone_number = request.form.get("phone_number", "").strip()
            notification_email = request.form.get("notification_email", "").strip() or email
            escalation_phone_number = request.form.get("escalation_phone_number", "").strip() or phone_number
            notes = request.form.get("notes", "").strip()
            direction = request.form.get("direction", "").strip()
            client_id = create_client(
                db,
                consultant_id=consultant_id,
                display_name=display_name,
                email=email,
                phone_number=phone_number,
                notification_email=notification_email,
                escalation_phone_number=escalation_phone_number,
                notes=notes,
                direction=direction,
            )
            log_audit(
                db,
                actor_type="consultant",
                actor_id=consultant_id,
                action="client_created",
                target_type="client",
                target_id=client_id,
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                user_agent=request.headers.get("User-Agent", ""),
            )
            db.commit()
            db.close()
            flash("Client created", "muted")
            return redirect(url_for("web.consultant_client_detail", client_id=client_id))
    clients = list_clients_for_consultant(db, consultant_id)
    db.close()
    return render_template(
        "consultant/clients.html",
        brand=current_app.config["BRAND_NAME"],
        clients=clients,
    )


@web_bp.get("/consultant/clients/<client_id>")
@require_consultant
def consultant_client_detail(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)
    sessions = list_sessions_for_client(db, client_id, limit=20)
    open_alerts = db.execute(
        """
        SELECT *
        FROM session_alerts
        WHERE client_id = ? AND acknowledged_at IS NULL
        ORDER BY created_at DESC
        """,
        (client_id,),
    ).fetchall()
    storage = _storage()
    latest_summary = None
    baseline = None
    if client["latest_summary_storage_key"]:
        latest_summary = storage.get_json(client["latest_summary_storage_key"], client_id)
    if client["baseline_storage_key"]:
        baseline = storage.get_json(client["baseline_storage_key"], client_id)
    db.close()
    return render_template(
        "consultant/client_detail.html",
        brand=current_app.config["BRAND_NAME"],
        client=client,
        sessions=sessions,
        open_alerts=open_alerts,
        latest_summary=latest_summary,
        baseline=baseline,
    )


@web_bp.get("/consultant/sessions")
@require_consultant
def consultant_sessions():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    sessions = list_sessions(db, consultant_id=consultant_id, limit=100)
    db.close()
    return render_template(
        "consultant/sessions.html",
        brand=current_app.config["BRAND_NAME"],
        sessions=sessions,
    )


@web_bp.get("/consultant/sessions/<session_id>")
@require_consultant
def consultant_session_detail(session_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    session_row = get_session_detail(db, session_id, consultant_id=consultant_id)
    if not session_row:
        db.close()
        abort(404)
    storage = _storage()
    summary = None
    biomarkers = None
    if session_row["summary_storage_key"]:
        summary = storage.get_json(session_row["summary_storage_key"], session_row["client_id"])
    if session_row["biomarker_storage_key"]:
        biomarkers = storage.get_json(session_row["biomarker_storage_key"], session_row["client_id"])
    alerts = db.execute(
        """
        SELECT *
        FROM session_alerts
        WHERE session_id = ?
        ORDER BY created_at DESC
        """,
        (session_id,),
    ).fetchall()
    db.close()
    return render_template(
        "consultant/session_detail.html",
        brand=current_app.config["BRAND_NAME"],
        session_row=session_row,
        summary=summary,
        biomarkers=biomarkers,
        alerts=alerts,
    )


@web_bp.get("/admin/dashboard")
@require_admin
def admin_dashboard():
    db = get_db(current_app.config)
    stats = {
        "consultants": db.execute("SELECT COUNT(*) AS c FROM consultants WHERE is_active = 1").fetchone()["c"],
        "clients": db.execute("SELECT COUNT(*) AS c FROM clients WHERE is_active = 1").fetchone()["c"],
        "sessions": db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
    }
    consultants = list_consultants(db)[:5]
    db.close()
    return render_template(
        "admin/dashboard.html",
        brand=current_app.config["BRAND_NAME"],
        stats=stats,
        admin_email=session.get("admin_email"),
        consultants=consultants,
    )


@web_bp.route("/admin/consultants", methods=["GET", "POST"])
@require_admin
def admin_consultants():
    db = get_db(current_app.config)
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        password = request.form.get("password", "").strip()
        if not email or not name or not phone_number or not password:
            flash("Email, name, phone number, and password are required", "error")
        else:
            from werkzeug.security import generate_password_hash

            try:
                create_consultant(
                    db,
                    email=email,
                    name=name,
                    phone_number=phone_number,
                    password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
                    notification_email=request.form.get("notification_email", "").strip() or email,
                    escalation_phone_number=request.form.get("escalation_phone_number", "").strip() or phone_number,
                )
                log_audit(
                    db,
                    actor_type="admin",
                    actor_id=session.get("admin_email", "unknown"),
                    action="consultant_created",
                    target_type="consultant",
                    target_id=email,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                )
                db.commit()
                flash("Consultant created", "muted")
                return redirect(url_for("web.admin_consultants"))
            except sqlite3.IntegrityError:
                db.rollback()
                flash("A consultant with that email already exists", "error")
    consultants = list_consultants(db)
    db.close()
    return render_template(
        "admin/consultants.html",
        brand=current_app.config["BRAND_NAME"],
        consultants=consultants,
    )
