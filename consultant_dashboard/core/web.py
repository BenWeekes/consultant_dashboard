import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask import jsonify

from .auth import require_admin, require_consultant
from .db import (
    create_client_access_link,
    create_client_message,
    create_client,
    create_consultant,
    deactivate_client,
    deactivate_consultant,
    delete_session,
    get_client_access_link_by_hash,
    get_client_detail,
    get_consultant_by_id,
    get_db,
    get_latest_session_artifacts,
    get_session_detail,
    list_recent_biomarker_keys,
    list_clients_for_consultant,
    list_client_messages,
    list_consultants,
    list_sessions,
    list_sessions_for_client,
    log_audit,
    mark_client_access_link_used,
    update_client,
    update_client_context,
    update_client_password,
    update_consultant,
    update_consultant_password,
    upsert_client_auth_identity,
)
from .client_identity import build_identity_hashes
from .messaging import (
    build_delivery_content,
    build_reply_link,
    choose_delivery_channel,
    default_expiry,
    deliver_email,
    deliver_sms,
    hash_access_token,
    new_access_token,
)
from .phone_numbers import country_options, infer_country_code, local_display_number, normalize_phone
from .realtime import publish_client_thread_update
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


def _message_preview_rows(db, client_id: str, consultant_id: str, limit: int = 20):
    rows = list_client_messages(db, client_id=client_id, consultant_id=consultant_id, limit=limit)
    return list(reversed(rows))


def _parse_iso_datetime(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _serialize_message_rows(rows):
    return [
        {
            "id": row["id"],
            "direction": row["direction"],
            "channel": row["channel"],
            "subject": row["subject"] or "",
            "body": row["body"],
            "delivery_status": row["delivery_status"],
            "delivery_error": row["delivery_error"] or "",
            "consultant_name": row["consultant_name"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _format_percent(value):
    if not isinstance(value, (int, float)):
        return None
    return f"{round(float(value) * 100)}%"


def _display_metric_label(key: str) -> str:
    labels = {
        "heart_rate_bpm": "BPM",
        "heart_rate": "BPM",
        "hrv_sdnn_ms": "HRV",
        "hrv": "HRV",
        "breathing_rate_bpm": "Breathing Rate",
        "breathing_rate": "Breathing Rate",
        "stress_index": "Stress Index",
        "low_self_esteem": "Low Self-Esteem",
        "depression_probability": "Depression",
        "anxiety_probability": "Anxiety",
        "anhedonia": "Anhedonia",
        "low_mood": "Low Mood",
        "sleep_issues": "Sleep Issues",
        "low_energy": "Low Energy",
        "appetite_issues": "Appetite Issues",
        "worthlessness_issues": "Worthlessness",
        "concentration_issues": "Concentration",
        "psychomotor_issues": "Psychomotor",
        "nervousness": "Nervousness",
        "uncontrollable_worry": "Uncontrollable Worry",
        "excessive_worry": "Excessive Worry",
        "trouble_relaxing": "Trouble Relaxing",
        "restlessness": "Restlessness",
        "irritability": "Irritability",
        "dread": "Dread",
    }
    return labels.get(key, key.replace("_", " ").title())


def _format_metric_triplet(metric, *, percent: bool = False):
    if not isinstance(metric, dict):
        return None
    avg = metric.get("avg")
    if not isinstance(avg, (int, float)):
        return None
    min_value = metric.get("min")
    max_value = metric.get("max")
    if percent:
        fmt = lambda v: f"{round(float(v) * 100)}%"
    else:
        fmt = lambda v: str(round(float(v)))
    if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
        return f"{fmt(min_value)} / {fmt(max_value)} / {fmt(avg)}"
    return f"{fmt(avg)} avg"


def _format_bpm_triplet(metric):
    if not isinstance(metric, dict):
        return None
    avg = metric.get("avg")
    min_value = metric.get("min")
    max_value = metric.get("max")
    if avg is None:
        return None
    avg_int = round(float(avg))
    if min_value is None or max_value is None:
        return f"{avg_int} avg"
    return f"{round(float(min_value))} / {round(float(max_value))} / {avg_int}"


def _main_emotion_label(voice_metrics):
    if not isinstance(voice_metrics, dict):
        return None
    emotion_keys = ["angry", "disgusted", "fearful", "happy", "other", "sad", "surprised"]
    ranked = []
    for key in emotion_keys:
        metric = voice_metrics.get(key)
        if isinstance(metric, dict):
            avg = metric.get("avg")
        else:
            avg = metric
        if isinstance(avg, (int, float)) and avg > 0:
            ranked.append((float(avg), key))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    value, key = ranked[0]
    return f"{key.replace('_', ' ').title()} ({round(value * 100)}%)"


def _build_latest_biomarker_highlights(latest_biomarkers):
    if not isinstance(latest_biomarkers, dict):
        return []
    voice = latest_biomarkers.get("voice") or {}
    vitals = latest_biomarkers.get("vitals") or {}
    heart_rate_metric = vitals.get("heart_rate_bpm") or vitals.get("heart_rate")
    items = [
        ("BPM", _format_bpm_triplet(heart_rate_metric)),
        ("Emotion", _main_emotion_label(voice)),
        ("Stress", _format_percent((voice.get("stress") or {}).get("avg") if isinstance(voice.get("stress"), dict) else voice.get("stress"))),
        ("Burnout", _format_percent((voice.get("burnout") or {}).get("avg") if isinstance(voice.get("burnout"), dict) else voice.get("burnout"))),
        ("Fatigue", _format_percent((voice.get("fatigue") or {}).get("avg") if isinstance(voice.get("fatigue"), dict) else voice.get("fatigue"))),
    ]
    return [{"label": label, "value": value} for label, value in items if value]


def _baseline_display_value(key: str, baseline):
    if not isinstance(baseline, dict):
        return None
    averages = baseline.get("averages") or {}
    value = averages.get(key)
    if not isinstance(value, (int, float)):
        return None
    if key in {"heart_rate_bpm", "heart_rate", "hrv_sdnn_ms", "hrv", "breathing_rate_bpm", "breathing_rate", "stress_index"}:
        return str(round(float(value)))
    return f"{round(float(value) * 100)}%"


def _grouped_biomarker_sections(biomarkers, baseline=None):
    if not isinstance(biomarkers, dict):
        return []
    voice = biomarkers.get("voice") or {}
    vitals = biomarkers.get("vitals") or {}
    safety = biomarkers.get("safety") or {}
    groups = [
        (
            "Vitals",
            vitals,
            ["heart_rate_bpm", "heart_rate", "hrv_sdnn_ms", "hrv", "breathing_rate_bpm", "breathing_rate", "stress_index"],
            False,
        ),
        (
            "Helios — Wellness",
            voice,
            ["distress", "stress", "burnout", "fatigue", "low_self_esteem"],
            True,
        ),
        (
            "Apollo — Clinical",
            voice,
            [
                "depression_probability", "anxiety_probability", "anhedonia", "low_mood", "sleep_issues",
                "low_energy", "appetite_issues", "worthlessness_issues", "concentration_issues",
                "psychomotor_issues", "nervousness", "uncontrollable_worry", "excessive_worry",
                "trouble_relaxing", "restlessness", "irritability", "dread",
            ],
            True,
        ),
        (
            "Emotions",
            voice,
            ["angry", "disgusted", "fearful", "happy", "neutral", "other", "sad", "surprised"],
            True,
        ),
    ]
    sections = []
    for title, source, keys, percent in groups:
        rows = []
        for key in keys:
            metric = source.get(key)
            if not isinstance(metric, dict):
                continue
            triplet = _format_metric_triplet(metric, percent=percent)
            if not triplet:
                continue
            rows.append(
                {
                    "label": _display_metric_label(key),
                    "triplet": triplet,
                    "baseline": _baseline_display_value(key, baseline),
                }
            )
        if rows:
            sections.append({"title": title, "rows": rows})
    safety_lines = []
    if safety.get("highest_level") is not None:
        safety_lines.append(f"Highest level: {safety['highest_level']}")
    if safety.get("highest_concerns"):
        safety_lines.append("Concerns: " + ", ".join(safety["highest_concerns"]))
    if safety.get("highest_recommended_actions"):
        safety_lines.append("Actions: " + "; ".join(safety["highest_recommended_actions"]))
    if safety_lines:
        sections.append({"title": "Safety", "rows": [{"label": "Summary", "triplet": line, "baseline": None} for line in safety_lines]})
    return sections


def _send_client_message(db, *, consultant_id: str, client, client_id: str, message_body: str):
    token = new_access_token()
    access_link_id = create_client_access_link(
        db,
        client_id=client_id,
        created_by=consultant_id,
        token_hash=hash_access_token(token),
        expires_at=default_expiry(),
    )
    reply_link = build_reply_link(current_app.config, token)
    delivery_channel = choose_delivery_channel(
        client_email=client["email"] or "",
        client_phone=client["phone_number"] or "",
    )
    _subject, outbound_body = build_delivery_content(
        channel=delivery_channel if delivery_channel != "portal" else "email",
        brand=current_app.config["BRAND_NAME"],
        client_name=client["display_name"],
        body=message_body,
    )
    if delivery_channel == "email":
        delivery_status, delivery_error = deliver_email(
            current_app.config,
            to_email=client["email"] or "",
            subject=f"{current_app.config['BRAND_NAME']} message",
            body=outbound_body,
            reply_link=reply_link,
            kind="message",
        )
    elif delivery_channel == "sms":
        delivery_status, delivery_error = deliver_sms(
            current_app.config,
            to_phone=client["phone_number"] or "",
            body=outbound_body,
            reply_link=reply_link,
        )
    else:
        delivery_status, delivery_error = "not_sent", "no_client_delivery_channel"

    create_client_message(
        db,
        client_id=client_id,
        consultant_id=consultant_id,
        direction="outbound",
        channel=delivery_channel,
        subject="",
        body=message_body,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
        access_link_id=access_link_id,
        metadata={"reply_link": reply_link},
    )
    log_audit(
        db,
        actor_type="consultant",
        actor_id=consultant_id,
        action="client_message_sent",
        target_type="client",
        target_id=client_id,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
        details={"channel": delivery_channel, "delivery_status": delivery_status},
    )
    return delivery_status, delivery_error


def _refresh_client_derived_state(db, storage: EncryptedStorage, client_id: str) -> None:
    latest = get_latest_session_artifacts(db, client_id)
    latest_summary_key = latest["summary_storage_key"] if latest else None
    biomarker_rows = list_recent_biomarker_keys(db, client_id, limit=5)

    baseline_key = None
    metrics = {}
    for row in biomarker_rows:
        payload = storage.get_json(row["biomarker_storage_key"], client_id)
        if not payload:
            continue
        for key, value in payload.get("averages", {}).items():
            if isinstance(value, (int, float)):
                metrics.setdefault(key, []).append(float(value))

    if biomarker_rows:
        baseline = {
            "window_sessions": len(biomarker_rows),
            "averages": {
                key: round(sum(values) / len(values), 4)
                for key, values in metrics.items()
                if values
            },
        }
        baseline_key = f"clients/{client_id}/baseline.json.enc"
        storage.put_json(baseline_key, client_id, baseline)

    db.execute(
        """
        UPDATE clients
        SET latest_summary_storage_key = ?,
            baseline_storage_key = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (latest_summary_key, baseline_key, client_id),
    )


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
            if initial_password and len(initial_password) < 8:
                flash("Initial password must be at least 8 characters.", "error")
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
        if request.form.get("form_name") == "send_message":
            message_body = request.form.get("message_body", "").strip()
            if not message_body:
                flash("Message is required", "error")
            else:
                delivery_status, _delivery_error = _send_client_message(
                    db,
                    consultant_id=consultant_id,
                    client=client,
                    client_id=client_id,
                    message_body=message_body,
                )
                db.commit()
                publish_client_thread_update(client_id)
                if delivery_status == "sent":
                    flash("Message sent", "muted")
                elif delivery_status == "not_sent":
                    flash("Message saved, but client notifications are not configured yet", "muted")
                else:
                    flash("Message saved, but delivery failed", "error")
            client = get_client_detail(db, client_id, consultant_id=consultant_id)
        elif request.form.get("form_name") == "save_context":
            update_client_context(
                db,
                client_id=client_id,
                notes=request.form.get("notes", "").strip(),
                direction=request.form.get("direction", "").strip(),
            )
            log_audit(
                db,
                actor_type="consultant",
                actor_id=consultant_id,
                action="client_context_updated",
                target_type="client",
                target_id=client_id,
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                user_agent=request.headers.get("User-Agent", ""),
            )
            db.commit()
            flash("Notes and direction updated", "muted")
            client = get_client_detail(db, client_id, consultant_id=consultant_id)
        else:
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
    latest_biomarkers = None
    latest_biomarker_highlights = []
    latest_biomarker_sections = []
    baseline = None
    if client["latest_summary_storage_key"]:
        latest_summary = storage.get_json(client["latest_summary_storage_key"], client_id)
    latest_session_row = sessions[0] if sessions else None
    if latest_session_row and latest_session_row["biomarker_storage_key"]:
        latest_biomarkers = storage.get_json(latest_session_row["biomarker_storage_key"], client_id)
        latest_biomarker_highlights = _build_latest_biomarker_highlights(latest_biomarkers)
        latest_biomarker_sections = _grouped_biomarker_sections(latest_biomarkers)
    if client["baseline_storage_key"]:
        baseline = storage.get_json(client["baseline_storage_key"], client_id)
    session_summaries = {}
    for session_row in sessions:
        summary_payload = None
        if session_row["summary_storage_key"]:
            summary_payload = storage.get_json(session_row["summary_storage_key"], client_id)
        session_summaries[session_row["id"]] = summary_payload or {}
    messages = _message_preview_rows(db, client_id, consultant_id, limit=20)
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
        latest_biomarkers=latest_biomarkers,
        latest_biomarker_highlights=latest_biomarker_highlights,
        latest_biomarker_sections=latest_biomarker_sections,
        baseline=baseline,
        session_summaries=session_summaries,
        messages=messages,
        phone_countries=country_options(),
        phone_form=_phone_form_value(client["phone_number"]),
        escalation_phone_form=_phone_form_value(client["escalation_phone_number"]),
    )


@web_bp.get("/consultant/clients/<client_id>/messages/thread")
@require_consultant
def consultant_client_messages_thread(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)
    messages = _serialize_message_rows(_message_preview_rows(db, client_id, consultant_id, limit=100))
    db.close()
    return jsonify({"messages": messages})


@web_bp.post("/consultant/clients/<client_id>/messages/send")
@require_consultant
def consultant_client_messages_send(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)
    message_body = (request.get_json(silent=True) or {}).get("body", "").strip()
    if not message_body:
        db.close()
        return jsonify({"error": "message required"}), 400
    delivery_status, delivery_error = _send_client_message(
        db,
        consultant_id=consultant_id,
        client=client,
        client_id=client_id,
        message_body=message_body,
    )
    db.commit()
    publish_client_thread_update(client_id)
    messages = _serialize_message_rows(_message_preview_rows(db, client_id, consultant_id, limit=100))
    db.close()
    return jsonify(
        {
            "ok": True,
            "delivery_status": delivery_status,
            "delivery_error": delivery_error,
            "messages": messages,
        }
    )


@web_bp.route("/consultant/clients/<client_id>/messages/new", methods=["GET", "POST"])
@require_consultant
def consultant_client_message_new(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)

    form_defaults = {
        "body": "",
    }
    if request.method == "POST":
        form_defaults = {
            "body": request.form.get("body", "").strip(),
        }
        if not form_defaults["body"]:
            flash("Message body is required", "error")
        else:
            token = new_access_token()
            access_link_id = create_client_access_link(
                db,
                client_id=client_id,
                created_by=consultant_id,
                token_hash=hash_access_token(token),
                expires_at=default_expiry(),
            )
            reply_link = build_reply_link(current_app.config, token)
            delivery_channel = choose_delivery_channel(
                client_email=client["email"] or "",
                client_phone=client["phone_number"] or "",
            )
            _subject, message_body = build_delivery_content(
                channel=delivery_channel if delivery_channel != "portal" else "email",
                brand=current_app.config["BRAND_NAME"],
                client_name=client["display_name"],
                body=form_defaults["body"],
            )
            if delivery_channel == "email":
                delivery_status, delivery_error = deliver_email(
                    current_app.config,
                    to_email=client["email"] or "",
                    subject=f"{current_app.config['BRAND_NAME']} message",
                    body=message_body,
                    reply_link=reply_link,
                    kind="message",
                )
            elif delivery_channel == "sms":
                delivery_status, delivery_error = deliver_sms(
                    current_app.config,
                    to_phone=client["phone_number"] or "",
                    body=message_body,
                    reply_link=reply_link,
                )
            else:
                delivery_status, delivery_error = "not_sent", "no_client_delivery_channel"

            create_client_message(
                db,
                client_id=client_id,
                consultant_id=consultant_id,
                direction="outbound",
                channel=delivery_channel,
                subject="",
                body=form_defaults["body"],
                delivery_status=delivery_status,
                delivery_error=delivery_error,
                access_link_id=access_link_id,
                metadata={"reply_link": reply_link},
            )
            log_audit(
                db,
                actor_type="consultant",
                    actor_id=consultant_id,
                    action="client_message_sent",
                    target_type="client",
                    target_id=client_id,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                    details={"channel": delivery_channel, "delivery_status": delivery_status},
                )
            db.commit()
            db.close()
            if delivery_status == "sent":
                flash("Message sent", "muted")
            elif delivery_status == "not_sent":
                flash("Message saved, but delivery is not configured yet", "muted")
            else:
                flash("Message saved, but delivery failed", "error")
            return redirect(url_for("web.consultant_client_detail", client_id=client_id))

    messages = _message_preview_rows(db, client_id, consultant_id, limit=20)
    db.close()
    return render_template(
        "consultant/message_compose.html",
        brand=current_app.config["BRAND_NAME"],
        theme="consultant",
        client=client,
        messages=messages,
        form_defaults=form_defaults,
    )


@web_bp.route("/client/messages/<token>", methods=["GET", "POST"])
def client_message_portal(token: str):
    db = get_db(current_app.config)
    link = get_client_access_link_by_hash(db, hash_access_token(token))
    if not link:
        db.close()
        abort(404)
    expires_at = _parse_iso_datetime(link["expires_at"])
    if expires_at and expires_at < datetime.now(timezone.utc):
        db.close()
        return render_template(
            "shared/client_message_portal.html",
            brand=current_app.config["BRAND_NAME"],
            expired=True,
            client=link,
            messages=[],
        ), 410

    mark_client_access_link_used(db, link["id"])
    db.commit()
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if not body:
            flash("Reply message is required", "error")
        else:
            create_client_message(
                db,
                client_id=link["client_id"],
                consultant_id=link["consultant_id"],
                direction="inbound",
                channel="portal",
                subject="",
                body=body,
                delivery_status="received",
                access_link_id=link["id"],
            )
            log_audit(
                db,
                actor_type="client",
                actor_id=link["client_id"],
                action="client_message_replied",
                target_type="client",
                target_id=link["client_id"],
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                user_agent=request.headers.get("User-Agent", ""),
                details={"access_link_id": link["id"]},
            )
            db.commit()
            publish_client_thread_update(link["client_id"])
            flash("Reply sent", "muted")

    messages = list(reversed(list_client_messages(db, client_id=link["client_id"], limit=50)))
    db.close()
    return render_template(
        "shared/client_message_portal.html",
        brand=current_app.config["BRAND_NAME"],
        client=link,
        expired=False,
        messages=messages,
    )


@web_bp.get("/client/messages/<token>/thread")
def client_message_portal_thread(token: str):
    db = get_db(current_app.config)
    link = get_client_access_link_by_hash(db, hash_access_token(token))
    if not link:
        db.close()
        abort(404)
    messages = _serialize_message_rows(list(reversed(list_client_messages(db, client_id=link["client_id"], limit=100))))
    db.close()
    return jsonify({"messages": messages})


@web_bp.post("/consultant/clients/<client_id>/delete")
@require_consultant
def consultant_client_delete(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)
    deactivate_client(db, client_id=client_id)
    log_audit(
        db,
        actor_type="consultant",
        actor_id=consultant_id,
        action="client_deleted",
        target_type="client",
        target_id=client_id,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
    )
    db.commit()
    db.close()
    flash("Client deleted", "muted")
    return redirect(url_for("web.consultant_clients"))


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


@web_bp.route("/consultant/sessions/<session_id>", methods=["GET", "POST"])
@require_consultant
def consultant_session_detail(session_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    session_row = get_session_detail(db, session_id, consultant_id=consultant_id)
    if not session_row:
        db.close()
        abort(404)

    if request.method == "POST":
        update_client_context(
            db,
            client_id=session_row["client_id"],
            notes=request.form.get("notes", "").strip(),
            direction=request.form.get("direction", "").strip(),
        )
        log_audit(
            db,
            actor_type="consultant",
            actor_id=consultant_id,
            action="client_context_updated_from_session",
            target_type="client",
            target_id=session_row["client_id"],
            session_id=session_id,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            user_agent=request.headers.get("User-Agent", ""),
        )
        db.commit()
        flash("Notes and direction updated", "muted")
        session_row = get_session_detail(db, session_id, consultant_id=consultant_id)

    storage = _storage()
    summary = None
    biomarkers = None
    baseline = None
    biomarker_sections = []
    biomarker_headlines = []
    if session_row["summary_storage_key"]:
        summary = storage.get_json(session_row["summary_storage_key"], session_row["client_id"])
    if session_row["biomarker_storage_key"]:
        biomarkers = storage.get_json(session_row["biomarker_storage_key"], session_row["client_id"])
        biomarker_headlines = _build_latest_biomarker_highlights(biomarkers)
    if session_row["baseline_storage_key"]:
        baseline = storage.get_json(session_row["baseline_storage_key"], session_row["client_id"])
    biomarker_sections = _grouped_biomarker_sections(biomarkers, baseline=baseline)
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
        baseline=baseline,
        biomarker_headlines=biomarker_headlines,
        biomarker_sections=biomarker_sections,
        alerts=alerts,
        client_notes=session_row["notes_current"],
        client_direction=session_row["direction_current"],
    )


@web_bp.post("/consultant/sessions/<session_id>/delete")
@require_consultant
def consultant_session_delete(session_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    session_row = get_session_detail(db, session_id, consultant_id=consultant_id)
    if not session_row:
        db.close()
        abort(404)
    storage = _storage()
    client_id = session_row["client_id"]
    delete_session(db, session_id=session_id)
    _refresh_client_derived_state(db, storage, client_id)
    log_audit(
        db,
        actor_type="consultant",
        actor_id=consultant_id,
        action="session_deleted",
        target_type="session",
        target_id=session_id,
        session_id=session_id,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
        details={"client_id": client_id},
    )
    db.commit()
    db.close()
    flash("Session deleted", "muted")
    return redirect(url_for("web.consultant_client_detail", client_id=client_id))


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
