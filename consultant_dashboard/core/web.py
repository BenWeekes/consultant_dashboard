import sqlite3

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from .auth import require_admin, require_consultant
from .db import (
    create_client,
    create_consultant,
    deactivate_consultant,
    get_client_detail,
    get_consultant_by_id,
    get_db,
    get_session_detail,
    list_clients_for_consultant,
    list_consultants,
    list_sessions,
    list_sessions_for_client,
    log_audit,
    update_client,
    update_client_password,
    update_consultant,
    update_consultant_password,
    upsert_client_auth_identity,
)
from .client_identity import build_identity_hashes
from .phone_numbers import country_options, infer_country_code, local_display_number, normalize_phone
from .storage import EncryptedStorage
from werkzeug.security import generate_password_hash

web_bp = Blueprint("web", __name__)


def _phone_form_value(phone_number: str):
    return {
        "country_code": infer_country_code(phone_number),
        "local_number": local_display_number(phone_number),
    }


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
        "linked_clients": db.execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM clients c
            JOIN consultant_clients cc ON cc.client_id = c.id
            JOIN client_auth_identities cai ON cai.client_id = c.id
            WHERE cc.consultant_id = ? AND c.is_active = 1
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
        theme="consultant",
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
    form_defaults = {
        "phone_country_code": "US",
        "escalation_phone_country_code": "US",
    }
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Client name is required", "error")
        else:
            email = request.form.get("email", "").strip()
            phone_country_code = request.form.get("phone_country_code", "US").strip().upper()
            escalation_phone_country_code = request.form.get("escalation_phone_country_code", phone_country_code).strip().upper()
            raw_phone_number = request.form.get("phone_number", "").strip()
            raw_escalation_phone = request.form.get("escalation_phone_number", "").strip()
            initial_password = request.form.get("initial_password", "").strip()
            if initial_password and (not email or not raw_phone_number):
                flash("Email and phone number are required when setting a client password.", "error")
                clients = list_clients_for_consultant(db, consultant_id)
                form_defaults = {
                    "display_name": display_name,
                    "email": email,
                    "initial_password": initial_password,
                    "phone_number": raw_phone_number,
                    "phone_country_code": phone_country_code,
                    "notification_email": request.form.get("notification_email", "").strip(),
                    "escalation_phone_number": raw_escalation_phone,
                    "escalation_phone_country_code": escalation_phone_country_code,
                    "notes": request.form.get("notes", "").strip(),
                    "direction": request.form.get("direction", "").strip(),
                }
                db.close()
                return render_template(
                    "consultant/clients.html",
                    brand=current_app.config["BRAND_NAME"],
                    theme="consultant",
                    clients=clients,
                    phone_countries=country_options(),
                    form_defaults=form_defaults,
                )
            try:
                phone_number = normalize_phone(raw_phone_number, phone_country_code) if raw_phone_number else ""
                escalation_phone_number = (
                    normalize_phone(raw_escalation_phone, escalation_phone_country_code)
                    if raw_escalation_phone
                    else phone_number
                )
            except ValueError as exc:
                flash(str(exc), "error")
                form_defaults = {
                    "display_name": display_name,
                    "email": email,
                    "initial_password": initial_password,
                    "phone_number": raw_phone_number,
                    "phone_country_code": phone_country_code,
                    "notification_email": request.form.get("notification_email", "").strip(),
                    "escalation_phone_number": raw_escalation_phone,
                    "escalation_phone_country_code": escalation_phone_country_code,
                    "notes": request.form.get("notes", "").strip(),
                    "direction": request.form.get("direction", "").strip(),
                }
                clients = list_clients_for_consultant(db, consultant_id)
                db.close()
                return render_template(
                    "consultant/clients.html",
                    brand=current_app.config["BRAND_NAME"],
                    theme="consultant",
                    clients=clients,
                    phone_countries=country_options(),
                    form_defaults=form_defaults,
                )
            notification_email = request.form.get("notification_email", "").strip() or email
            notes = request.form.get("notes", "").strip()
            direction = request.form.get("direction", "").strip()
            client_id = create_client(
                db,
                consultant_id=consultant_id,
                display_name=display_name,
                email=email,
                password_hash=generate_password_hash(initial_password, method="pbkdf2:sha256") if initial_password else "",
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
        theme="consultant",
        clients=clients,
        phone_countries=country_options(),
        form_defaults=form_defaults,
    )


@web_bp.route("/consultant/clients/<client_id>", methods=["GET", "POST"])
@require_consultant
def consultant_client_detail(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)

    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        email = request.form.get("email", "").strip()
        phone_country_code = request.form.get("phone_country_code", "US").strip().upper()
        escalation_phone_country_code = request.form.get("escalation_phone_country_code", phone_country_code).strip().upper()
        raw_phone_number = request.form.get("phone_number", "").strip()
        raw_escalation_phone = request.form.get("escalation_phone_number", "").strip()
        notification_email = request.form.get("notification_email", "").strip() or email
        notes = request.form.get("notes", "").strip()
        direction = request.form.get("direction", "").strip()
        reset_password = request.form.get("reset_password", "").strip()

        if not display_name:
            flash("Client name is required", "error")
        else:
            try:
                if reset_password and (not email or not raw_phone_number):
                    raise ValueError("Email and phone number are required when setting a client password.")
                phone_number = normalize_phone(raw_phone_number, phone_country_code) if raw_phone_number else ""
                escalation_phone_number = (
                    normalize_phone(raw_escalation_phone, escalation_phone_country_code)
                    if raw_escalation_phone
                    else phone_number
                )
                update_client(
                    db,
                    client_id=client_id,
                    display_name=display_name,
                    email=email,
                    phone_number=phone_number,
                    notification_email=notification_email,
                    escalation_phone_number=escalation_phone_number,
                    notes=notes,
                    direction=direction,
                )
                if reset_password:
                    if len(reset_password) < 8:
                        raise ValueError("Temporary password must be at least 8 characters.")
                    update_client_password(
                        db,
                        client_id=client_id,
                        password_hash=generate_password_hash(reset_password, method="pbkdf2:sha256"),
                    )
                    log_audit(
                        db,
                        actor_type="consultant",
                        actor_id=consultant_id,
                        action="client_password_reset",
                        target_type="client",
                        target_id=client_id,
                        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                        user_agent=request.headers.get("User-Agent", ""),
                    )
                identity_hashes = build_identity_hashes(display_name, email, phone_number)
                if any(identity_hashes.values()):
                    upsert_client_auth_identity(
                        db,
                        client_id=client_id,
                        email_hash=identity_hashes["email_hash"],
                        normalized_name_hash=identity_hashes["normalized_name_hash"],
                        phone_hash=identity_hashes["phone_hash"],
                    )
                log_audit(
                    db,
                    actor_type="consultant",
                    actor_id=consultant_id,
                    action="client_updated",
                    target_type="client",
                    target_id=client_id,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                )
                db.commit()
                flash("Client updated", "muted")
                if reset_password:
                    flash("Temporary client password updated", "muted")
            except ValueError as exc:
                db.rollback()
                flash(str(exc), "error")
            client = get_client_detail(db, client_id, consultant_id=consultant_id)

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
    auth_identity = db.execute(
        """
        SELECT *
        FROM client_auth_identities
        WHERE client_id = ?
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()
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
        theme="consultant",
        client=client,
        sessions=sessions,
        open_alerts=open_alerts,
        auth_identity=auth_identity,
        latest_summary=latest_summary,
        baseline=baseline,
        phone_countries=country_options(),
        phone_form=_phone_form_value(client["phone_number"]),
        escalation_phone_form=_phone_form_value(client["escalation_phone_number"]),
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
        theme="consultant",
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
        theme="consultant",
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
    form_defaults = {
        "phone_country_code": "US",
        "escalation_phone_country_code": "US",
    }
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        phone_country_code = request.form.get("phone_country_code", "US").strip().upper()
        escalation_phone_country_code = request.form.get("escalation_phone_country_code", phone_country_code).strip().upper()
        raw_phone_number = request.form.get("phone_number", "").strip()
        raw_escalation_phone = request.form.get("escalation_phone_number", "").strip()
        password = request.form.get("password", "").strip()
        if not email or not name or not raw_phone_number or not password:
            flash("Email, name, phone number, and password are required", "error")
        else:
            try:
                phone_number = normalize_phone(raw_phone_number, phone_country_code)
                escalation_phone_number = (
                    normalize_phone(raw_escalation_phone, escalation_phone_country_code)
                    if raw_escalation_phone
                    else phone_number
                )
            except ValueError as exc:
                flash(str(exc), "error")
                consultants = list_consultants(db)
                form_defaults = {
                    "name": name,
                    "email": email,
                    "phone_number": raw_phone_number,
                    "phone_country_code": phone_country_code,
                    "notification_email": request.form.get("notification_email", "").strip(),
                    "escalation_phone_number": raw_escalation_phone,
                    "escalation_phone_country_code": escalation_phone_country_code,
                    "password": password,
                }
                db.close()
                return render_template(
                    "admin/consultants.html",
                    brand=current_app.config["BRAND_NAME"],
                    consultants=consultants,
                    phone_countries=country_options(),
                    form_defaults=form_defaults,
                )

            try:
                create_consultant(
                    db,
                    email=email,
                    name=name,
                    phone_number=phone_number,
                    password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
                    notification_email=request.form.get("notification_email", "").strip() or email,
                    escalation_phone_number=escalation_phone_number,
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
        phone_countries=country_options(),
        form_defaults=form_defaults,
    )


@web_bp.route("/admin/consultants/<consultant_id>", methods=["GET", "POST"])
@require_admin
def admin_consultant_detail(consultant_id: str):
    db = get_db(current_app.config)
    consultant = get_consultant_by_id(db, consultant_id)
    if not consultant:
        db.close()
        abort(404)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        phone_country_code = request.form.get("phone_country_code", "US").strip().upper()
        escalation_phone_country_code = request.form.get("escalation_phone_country_code", phone_country_code).strip().upper()
        raw_phone_number = request.form.get("phone_number", "").strip()
        notification_email = request.form.get("notification_email", "").strip() or email
        raw_escalation_phone = request.form.get("escalation_phone_number", "").strip()
        reset_password = request.form.get("reset_password", "").strip()

        if not email or not name or not raw_phone_number:
            flash("Email, name, and phone number are required", "error")
        else:
            try:
                phone_number = normalize_phone(raw_phone_number, phone_country_code)
                escalation_phone_number = (
                    normalize_phone(raw_escalation_phone, escalation_phone_country_code)
                    if raw_escalation_phone
                    else phone_number
                )
                update_consultant(
                    db,
                    consultant_id=consultant_id,
                    email=email,
                    name=name,
                    phone_number=phone_number,
                    notification_email=notification_email,
                    escalation_phone_number=escalation_phone_number,
                )
                if reset_password:
                    if len(reset_password) < 8:
                        raise ValueError("Temporary password must be at least 8 characters.")
                    update_consultant_password(
                        db,
                        consultant_id=consultant_id,
                        password_hash=generate_password_hash(reset_password, method="pbkdf2:sha256"),
                    )
                    log_audit(
                        db,
                        actor_type="admin",
                        actor_id=session.get("admin_email", "unknown"),
                        action="consultant_password_reset",
                        target_type="consultant",
                        target_id=consultant_id,
                        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                        user_agent=request.headers.get("User-Agent", ""),
                    )
                log_audit(
                    db,
                    actor_type="admin",
                    actor_id=session.get("admin_email", "unknown"),
                    action="consultant_updated",
                    target_type="consultant",
                    target_id=consultant_id,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                )
                db.commit()
                flash("Consultant updated", "muted")
                if reset_password:
                    flash("Temporary password updated", "muted")
            except sqlite3.IntegrityError:
                db.rollback()
                flash("A consultant with that email already exists", "error")
            except ValueError as exc:
                db.rollback()
                flash(str(exc), "error")
            consultant = get_consultant_by_id(db, consultant_id)

    db.close()
    return render_template(
        "admin/consultant_detail.html",
        brand=current_app.config["BRAND_NAME"],
        consultant=consultant,
        phone_countries=country_options(),
        phone_form=_phone_form_value(consultant["phone_number"]),
        escalation_phone_form=_phone_form_value(consultant["escalation_phone_number"]),
    )


@web_bp.post("/admin/consultants/<consultant_id>/delete")
@require_admin
def admin_consultant_delete(consultant_id: str):
    db = get_db(current_app.config)
    consultant = get_consultant_by_id(db, consultant_id)
    if not consultant:
        db.close()
        abort(404)

    deactivate_consultant(db, consultant_id=consultant_id)
    log_audit(
        db,
        actor_type="admin",
        actor_id=session.get("admin_email", "unknown"),
        action="consultant_deleted",
        target_type="consultant",
        target_id=consultant_id,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
    )
    db.commit()
    db.close()
    flash("Consultant deleted", "muted")
    return redirect(url_for("web.admin_consultants"))
