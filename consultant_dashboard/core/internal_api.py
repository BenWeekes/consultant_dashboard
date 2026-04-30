import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import check_password_hash

from .db import (
    complete_scheduled_meeting,
    create_escalation_event,
    create_session_alert,
    get_client_detail,
    get_client_by_email,
    get_client_access_link_by_hash,
    get_client_context,
    get_meeting_by_response_access_link_id,
    get_db,
    get_latest_escalation_event,
    get_meeting_signal_flags,
    get_scheduled_meeting,
    get_vendor_by_slug,
    log_audit,
    mark_meeting_participant_left,
    mark_meeting_joined,
    resolve_client_identity,
    record_meeting_event,
    update_escalation_event,
    upsert_session,
)
from .agora_tokens import build_rtc_token
from .meetings import utc_now
from .messaging import hash_access_token
from .storage import EncryptedStorage
from .vendors import storage_root_for_client
from .web import _ensure_next_weekly_occurrence, _refresh_client_derived_state, run_due_meeting_reminders

internal_bp = Blueprint("internal", __name__, url_prefix="/internal")


def _verify_internal_request() -> None:
    if request.path == "/internal/health":
        return
    ts = request.headers.get("X-Consultant-Timestamp", "")
    sig = request.headers.get("X-Consultant-Signature", "")
    if not ts or not sig:
        raise PermissionError("Missing internal auth headers")
    try:
        ts_i = int(ts)
    except ValueError as exc:
        raise PermissionError("Invalid timestamp") from exc
    if abs(int(time.time()) - ts_i) > 300:
        raise PermissionError("Expired timestamp")
    payload = request.get_data(as_text=True) if request.method != "GET" else request.query_string.decode("utf-8")
    canonical = f"{ts}.{request.method}.{request.path}.{payload}".encode("utf-8")
    expected = hmac.new(
        current_app.config["INTERNAL_SHARED_SECRET"].encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise PermissionError("Invalid signature")


@internal_bp.before_request
def _auth():
    try:
        _verify_internal_request()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    return None


@internal_bp.get("/health")
def internal_health():
    return {
        "status": "ok",
        "service": "consultant-dashboard",
        "db_path": current_app.config["DB_PATH"],
    }


@internal_bp.get("/resolve-client")
def resolve_client():
    db = get_db(current_app.config)
    vendor = get_vendor_by_slug(db, request.args.get("vendor_slug", "").strip())
    row = resolve_client_identity(
        db,
        vendor_id=vendor["id"] if vendor else "",
        google_sub_hash=request.args.get("google_sub_hash", ""),
        email_hash=request.args.get("email_hash", ""),
        normalized_name_hash=request.args.get("normalized_name_hash", ""),
        phone_hash=request.args.get("phone_hash", ""),
    )
    db.close()
    if not row:
        return jsonify({"found": False}), 404
    return {
        "found": True,
        "client_id": row["client_id"],
        "consultant_id": row["consultant_id"],
        "vendor_slug": request.args.get("vendor_slug", "").strip().lower(),
        "is_active": bool(row["is_active"]),
        "email": row["email"] or "",
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "display_name": row["display_name"] or "",
        "phone_number": row["phone_number"] or "",
    }


@internal_bp.get("/client-context")
def client_context():
    client_id = request.args.get("client_id", "").strip()
    if not client_id:
        return jsonify({"error": "client_id required"}), 400
    db = get_db(current_app.config)
    result = get_client_context(db, client_id)
    if not result:
        db.close()
        return jsonify({"error": "client not found"}), 404
    client, alerts = result
    storage = EncryptedStorage(storage_root_for_client(client_id), current_app.config["MASTER_KEY"])
    if not client["ai_summary_storage_key"] or not client["human_summary_storage_key"]:
        _refresh_client_derived_state(db, storage, client_id)
        db.commit()
        refreshed = get_client_context(db, client_id)
        if refreshed:
            client, alerts = refreshed
    latest_summary = None
    baseline = None
    ai_personal_summary = None
    human_personal_summary = None
    recent_summaries = []
    if client["latest_summary_storage_key"]:
        latest_summary = _normalize_summary_payload(
            storage.get_json(client["latest_summary_storage_key"], client_id)
        )
    if client["baseline_storage_key"]:
        baseline = storage.get_json(client["baseline_storage_key"], client_id)
    if client["ai_summary_storage_key"]:
        ai_personal_summary = storage.get_json(client["ai_summary_storage_key"], client_id)
    if client["human_summary_storage_key"]:
        human_personal_summary = storage.get_json(client["human_summary_storage_key"], client_id)
    recent_summaries = _load_recent_summaries(storage, db, client_id)
    db.close()
    return {
        "client_id": client["id"],
        "display_name": client["display_name"],
        "first_name": client["first_name"] or "",
        "last_name": client["last_name"] or "",
        "year_of_birth": client["year_of_birth"],
        "sex": client["sex"] or "",
        "consultant_id": client["consultant_id"],
        "consultant_name": client["consultant_name"],
        "notes": client["notes_current"],
        "direction": client["direction_current"],
        "latest_summary": latest_summary,
        "ai_personal_summary": ai_personal_summary,
        "human_personal_summary": human_personal_summary,
        "ai_session_count": int(client["ai_session_count"] or 0),
        "human_session_count": int(client["human_session_count"] or 0),
        "recent_summaries": recent_summaries,
        "baseline": baseline,
        "alerts": [dict(a) for a in alerts],
    }


@internal_bp.get("/meeting-signals")
def meeting_signals():
    meeting_id = request.args.get("meeting_id", "").strip()
    if not meeting_id:
        return jsonify({"error": "meeting_id required"}), 400
    db = get_db(current_app.config)
    meeting = get_scheduled_meeting(db, meeting_id)
    if not meeting:
        db.close()
        return jsonify({"error": "meeting not found"}), 404
    signal_flags = get_meeting_signal_flags(db, meeting_id)
    db.close()
    return {
        "ok": True,
        "meeting_id": meeting_id,
        "meeting_type": meeting["meeting_type"] or "",
        "transcription_enabled": bool(signal_flags["transcription_enabled"]),
        "audio_biomarkers_enabled": bool(signal_flags["audio_biomarkers_enabled"]),
        "video_biomarkers_enabled": bool(signal_flags["video_biomarkers_enabled"]),
    }


@internal_bp.post("/verify-client-password")
def verify_client_password():
    payload = request.get_json(force=True)
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400

    db = get_db(current_app.config)
    vendor = get_vendor_by_slug(db, (payload.get("vendor_slug") or "").strip())
    client = get_client_by_email(db, email, vendor_id=vendor["id"] if vendor else "")
    db.close()
    if not client:
        return jsonify({"error": "invalid_credentials"}), 401
    if not client["password_hash"]:
        return jsonify({"error": "password_login_not_enabled"}), 403
    if not check_password_hash(client["password_hash"], password):
        return jsonify({"error": "invalid_credentials"}), 401

    return {
        "ok": True,
        "client_id": client["id"],
        "consultant_id": client["consultant_id"],
        "vendor_slug": (payload.get("vendor_slug") or "").strip().lower(),
        "first_name": client["first_name"] or "",
        "last_name": client["last_name"] or "",
        "display_name": client["display_name"],
        "email": client["email"],
        "phone_number": client["phone_number"],
        "is_active": bool(client["is_active"]),
    }


@internal_bp.post("/crisis-escalate-init")
def crisis_escalate_init():
    payload = request.get_json(force=True)
    meeting_id = (payload.get("meeting_id") or "").strip()
    client_id = (payload.get("client_id") or "").strip()
    session_id = (payload.get("session_id") or "").strip()
    channel_name = (payload.get("channel_name") or "").strip()
    source = (payload.get("source") or "thymia").strip()
    if not client_id or not session_id:
        return jsonify({"error": "client_id and session_id required"}), 400
    try:
        level = int(payload["level"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "numeric level required"}), 400

    db = get_db(current_app.config)
    meeting = get_scheduled_meeting(db, meeting_id) if meeting_id else None
    client = get_client_detail(db, client_id)
    if not client:
        db.close()
        return jsonify({"error": "client not found"}), 404
    if meeting and meeting["client_id"] != client_id:
        db.close()
        return jsonify({"error": "meeting/client mismatch"}), 409
    if not meeting and not channel_name:
        db.close()
        return jsonify({"error": "channel_name required when meeting_id is missing"}), 400

    existing = get_latest_escalation_event(db, meeting_id=meeting_id, session_id="" if meeting_id else session_id)
    if existing and existing["status"] in {"initialised", "dialing", "answered"}:
        response = {
            "ok": True,
            "escalate": True,
            "escalation_event_id": existing["id"],
            "meeting_id": meeting_id,
            "channel_name": meeting["channel_name"] if meeting else channel_name,
            "client_id": client_id,
            "client_display_name": meeting["client_name"] if meeting else client["display_name"],
            "escalation_phone_number": existing["escalation_phone_number"] or "",
            "from_phone": current_app.config.get("CRISIS_CALL_FROM_NUMBER", ""),
            "sip_gateway": current_app.config.get("CRISIS_CALL_SIP_GATEWAY", ""),
            "region": current_app.config.get("CRISIS_CALL_REGION", "AREA_CODE_NA"),
            "pstn_uid": current_app.config.get("CRISIS_CALL_PSTN_UID", "43455"),
            "rtc_token": build_rtc_token(
                current_app.config.get("APP_ID", ""),
                current_app.config.get("APP_CERTIFICATE", ""),
                meeting["channel_name"] if meeting else channel_name,
                current_app.config.get("CRISIS_CALL_PSTN_UID", "43455"),
            ),
            "reason": existing["reason"] or "",
        }
        db.close()
        return response

    escalation_phone = ((meeting["client_escalation_phone_number"] if meeting else client["escalation_phone_number"]) or "").strip()
    alert = (payload.get("alert") or "").strip()
    if not escalation_phone:
        event_id = create_escalation_event(
            db,
            meeting_id=meeting_id,
            session_id=session_id,
            client_id=client_id,
            source=source,
            safety_level=level,
            alert=alert,
            status="skipped",
            reason="missing_phone",
            escalation_phone_number="",
        )
        log_audit(
            db,
            actor_type="system",
            actor_id="server-custom-llm",
            action="crisis_escalation_skipped",
            target_type="meeting",
            target_id=meeting_id,
            session_id=session_id,
            details={"client_id": client_id, "reason": "missing_phone"},
        )
        db.commit()
        db.close()
        return {
            "ok": True,
            "escalate": False,
            "reason": "missing_phone",
            "escalation_event_id": event_id,
        }

    event_id = create_escalation_event(
        db,
        meeting_id=meeting_id,
        session_id=session_id,
        client_id=client_id,
        source=source,
        safety_level=level,
        alert=alert,
        status="initialised",
        escalation_phone_number=escalation_phone,
    )
    log_audit(
        db,
        actor_type="system",
        actor_id="server-custom-llm",
        action="crisis_escalation_initialised",
        target_type="escalation_event",
        target_id=event_id,
        session_id=session_id,
        details={"client_id": client_id, "meeting_id": meeting_id, "channel_name": meeting["channel_name"] if meeting else channel_name},
    )
    db.commit()
    db.close()
    return {
        "ok": True,
        "escalate": True,
        "escalation_event_id": event_id,
        "meeting_id": meeting_id,
        "channel_name": meeting["channel_name"] if meeting else channel_name,
        "client_id": client_id,
        "client_display_name": meeting["client_name"] if meeting else client["display_name"],
        "escalation_phone_number": escalation_phone,
        "from_phone": current_app.config.get("CRISIS_CALL_FROM_NUMBER", ""),
        "sip_gateway": current_app.config.get("CRISIS_CALL_SIP_GATEWAY", ""),
        "region": current_app.config.get("CRISIS_CALL_REGION", "AREA_CODE_NA"),
        "pstn_uid": current_app.config.get("CRISIS_CALL_PSTN_UID", "43455"),
        "rtc_token": build_rtc_token(
            current_app.config.get("APP_ID", ""),
            current_app.config.get("APP_CERTIFICATE", ""),
            meeting["channel_name"] if meeting else channel_name,
            current_app.config.get("CRISIS_CALL_PSTN_UID", "43455"),
        ),
    }


CRISIS_ESCALATE_STATUS_ALLOWED_PHASES = frozenset(
    {"dialing", "answered", "failed", "completed"}
)


@internal_bp.post("/crisis-escalate-status")
def crisis_escalate_status():
    payload = request.get_json(force=True)
    escalation_event_id = (payload.get("escalation_event_id") or "").strip()
    phase = (payload.get("phase") or "").strip()
    if not escalation_event_id or not phase:
        return jsonify({"error": "escalation_event_id and phase required"}), 400
    if phase not in CRISIS_ESCALATE_STATUS_ALLOWED_PHASES:
        return jsonify({"error": f"invalid phase: {phase}"}), 400

    db = get_db(current_app.config)
    updated = update_escalation_event(
        db,
        escalation_event_id=escalation_event_id,
        status=phase,
        reason=(payload.get("reason") or "").strip(),
        provider_result=(payload.get("provider_result") or "").strip(),
        client_announcement_text=payload.get("client_announcement_text"),
        recipient_summary_text=payload.get("recipient_summary_text"),
    )
    if not updated:
        db.close()
        return jsonify({"error": "escalation event not found"}), 404
    log_audit(
        db,
        actor_type="system",
        actor_id="server-custom-llm",
        action=f"crisis_escalation_{phase}",
        target_type="escalation_event",
        target_id=escalation_event_id,
        session_id=(payload.get("session_id") or "").strip(),
        details={"reason": (payload.get("reason") or "").strip()},
    )
    db.commit()
    db.close()
    return {"ok": True, "escalation_event_id": escalation_event_id, "phase": phase}


def _parse_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_meeting_join_response(meeting, *, participant_role: str, participant_uid: str, ensure_services: bool):
    app_id = current_app.config.get("APP_ID") or "app"
    meeting_runtime_key = f"{app_id}:{meeting['channel_name']}:{meeting['id']}"
    return {
        "ok": True,
        "meeting_id": meeting["id"],
        "participant_role": participant_role,
        "client_id": meeting["client_id"],
        "consultant_id": meeting["consultant_id"],
        "channel_name": meeting["channel_name"],
        "meeting_runtime_key": meeting_runtime_key,
        "participant_uid": participant_uid,
        "user_uid": "101",
        "host_uid": "103",
        "guest_uid": "101",
        "rtm_uid": f"5001-{meeting['channel_name']}",
        "scheduled_start_at": meeting["scheduled_start_at"],
        "scheduled_end_at": meeting["scheduled_end_at"],
        "join_window_start_at": meeting["join_window_start_at"],
        "join_window_end_at": meeting["join_window_end_at"],
        "transcription_enabled": bool(meeting["transcription_enabled"]),
        "audio_biomarkers_enabled": bool(meeting["audio_biomarkers_enabled"]),
        "video_biomarkers_enabled": bool(meeting["video_biomarkers_enabled"]),
        "transcription_provider": meeting["transcription_provider"] or "",
        "transcription_language": meeting["transcription_language"] or "",
        "ensure_meeting_services": ensure_services,
    }


def _should_ensure_meeting_services(meeting) -> bool:
    if not meeting:
        return False
    return not bool(meeting["in_progress_at"])


def _meeting_uses_stable_pair_room(meeting) -> bool:
    return bool(meeting) and (meeting["meeting_type"] or "human").strip().lower() == "human"


def _host_end_should_complete_meeting(meeting) -> bool:
    if not meeting:
        return False
    if meeting["client_joined_at"]:
        return True
    scheduled_start_at = _parse_dt(meeting["scheduled_start_at"] or "")
    if not scheduled_start_at:
        return False
    return utc_now() >= scheduled_start_at.astimezone(timezone.utc)


@internal_bp.post("/authorize-meeting-join")
def authorize_meeting_join():
    payload = request.get_json(force=True)
    participant_role = (payload.get("participant_role") or "").strip().lower()
    if participant_role not in {"host", "guest"}:
        return jsonify({"error": "participant_role must be host or guest"}), 400

    db = get_db(current_app.config)
    meeting = None

    if participant_role == "guest":
        access_link_id = (payload.get("response_access_link_id") or "").strip()
        meeting_id = (payload.get("meeting_id") or "").strip()
        access_token = (payload.get("access_token") or "").strip()
        link = None
        if access_link_id:
            meeting = get_meeting_by_response_access_link_id(db, access_link_id)
            if not meeting:
                db.close()
                return jsonify({"error": "meeting_not_found"}), 404
            if meeting_id and meeting["id"] != meeting_id:
                db.close()
                return jsonify({"error": "meeting_not_found"}), 404
        else:
            if not access_token:
                db.close()
                return jsonify({"error": "access_token or response_access_link_id required for guest join"}), 400
            link = get_client_access_link_by_hash(db, hash_access_token(access_token))
            if not link:
                db.close()
                return jsonify({"error": "meeting_access_not_found"}), 404
            expires_at = _parse_dt(link["expires_at"])
            if expires_at and expires_at < utc_now():
                db.close()
                return jsonify({"error": "meeting_access_expired"}), 410
            meeting = get_meeting_by_response_access_link_id(db, link["id"])
        if access_link_id:
            link = db.execute(
                "SELECT * FROM client_access_links WHERE id = ? LIMIT 1",
                (access_link_id,),
            ).fetchone()
            if not link:
                db.close()
                return jsonify({"error": "meeting_access_not_found"}), 404
            expires_at = _parse_dt(link["expires_at"])
            if expires_at and expires_at < utc_now():
                db.close()
                return jsonify({"error": "meeting_access_expired"}), 410
        if not meeting:
            db.close()
            return jsonify({"error": "meeting_not_found"}), 404
        if not _meeting_uses_stable_pair_room(meeting) and meeting["status"] not in {"scheduled", "client_viewed", "accepted", "declined", "in_progress"}:
            db.close()
            return jsonify({"error": "meeting_not_joinable_for_guest"}), 403
    else:
        consultant_id = (payload.get("consultant_id") or "").strip()
        meeting_id = (payload.get("meeting_id") or "").strip()
        if not consultant_id or not meeting_id:
            db.close()
            return jsonify({"error": "consultant_id and meeting_id required for host join"}), 400
        meeting = get_scheduled_meeting(db, meeting_id)
        if not meeting:
            db.close()
            return jsonify({"error": "meeting_not_found"}), 404
        if meeting["consultant_id"] != consultant_id:
            db.close()
            return jsonify({"error": "meeting_not_owned_by_host"}), 403
        if not _meeting_uses_stable_pair_room(meeting) and meeting["status"] not in {"scheduled", "client_viewed", "accepted", "in_progress"}:
            db.close()
            return jsonify({"error": "meeting_not_joinable_for_host"}), 403

    if not _meeting_uses_stable_pair_room(meeting):
        if meeting["status"] == "cancelled":
            db.close()
            return jsonify({"error": "meeting_cancelled"}), 403
        if meeting["status"] == "declined":
            db.close()
            return jsonify({"error": "meeting_declined"}), 403
        if meeting["status"] == "completed":
            db.close()
            return jsonify({"error": "meeting_completed"}), 403

    now = utc_now()
    join_start = _parse_dt(meeting["join_window_start_at"])
    join_end = _parse_dt(meeting["join_window_end_at"])
    guest_join_start = _parse_dt(meeting["scheduled_start_at"])
    if guest_join_start and participant_role == "guest":
        guest_join_start = guest_join_start - timedelta(minutes=10)
    if join_start and now < join_start and not _meeting_uses_stable_pair_room(meeting):
        if participant_role == "host":
            db.close()
            return jsonify({"error": "meeting_too_early"}), 403
    if guest_join_start and participant_role == "guest" and now < guest_join_start and not _meeting_uses_stable_pair_room(meeting):
        db.close()
        return jsonify({"error": "meeting_too_early"}), 403
    if join_end and now > join_end and not _meeting_uses_stable_pair_room(meeting):
        db.close()
        return jsonify({"error": "meeting_join_window_expired"}), 403

    ensure_services = _should_ensure_meeting_services(meeting)
    event_type = "consultant_join_authorized" if participant_role == "host" else "client_join_authorized"
    actor_id = meeting["consultant_id"] if participant_role == "host" else meeting["client_id"]
    record_meeting_event(
        db,
        meeting_id=meeting["id"],
        actor_type=participant_role,
        actor_id=actor_id,
        event_type=event_type,
        details={"ensure_meeting_services": ensure_services},
    )
    db.commit()
    refreshed = get_scheduled_meeting(db, meeting["id"])
    db.close()

    participant_uid = "103" if participant_role == "host" else "101"
    return _build_meeting_join_response(
        refreshed,
        participant_role=participant_role,
        participant_uid=participant_uid,
        ensure_services=ensure_services,
    )


@internal_bp.post("/meeting-joined")
def meeting_joined():
    payload = request.get_json(force=True)
    meeting_id = (payload.get("meeting_id") or "").strip()
    participant_role = (payload.get("participant_role") or "").strip().lower()
    participant_id = (payload.get("participant_id") or "").strip()
    if not meeting_id or participant_role not in {"host", "guest"}:
        return jsonify({"error": "meeting_id and valid participant_role required"}), 400

    db = get_db(current_app.config)
    meeting = get_scheduled_meeting(db, meeting_id)
    if not meeting:
        db.close()
        return jsonify({"error": "meeting_not_found"}), 404

    ensure_services = mark_meeting_joined(db, meeting_id=meeting_id, participant_role=participant_role)
    if ensure_services is None and not _meeting_uses_stable_pair_room(meeting):
        db.close()
        return jsonify({"error": "meeting_not_joinable"}), 409
    if ensure_services is None:
        ensure_services = _should_ensure_meeting_services(meeting)
    record_meeting_event(
        db,
        meeting_id=meeting_id,
        actor_type=participant_role,
        actor_id=participant_id or (meeting["consultant_id"] if participant_role == "host" else meeting["client_id"]),
        event_type="participant_joined",
        details={"ensure_meeting_services": ensure_services},
    )
    db.commit()
    db.close()
    return {"ok": True, "ensure_meeting_services": ensure_services}


@internal_bp.post("/meeting-left")
def meeting_left():
    payload = request.get_json(force=True)
    meeting_id = (payload.get("meeting_id") or "").strip()
    participant_role = (payload.get("participant_role") or "").strip().lower()
    participant_id = (payload.get("participant_id") or "").strip()
    if not meeting_id or participant_role not in {"host", "guest"}:
        return jsonify({"error": "meeting_id and valid participant_role required"}), 400

    db = get_db(current_app.config)
    meeting = get_scheduled_meeting(db, meeting_id)
    if not meeting:
        db.close()
        return jsonify({"error": "meeting_not_found"}), 404

    mark_meeting_participant_left(db, meeting_id=meeting_id, participant_role=participant_role)
    record_meeting_event(
        db,
        meeting_id=meeting_id,
        actor_type=participant_role,
        actor_id=participant_id or (meeting["consultant_id"] if participant_role == "host" else meeting["client_id"]),
        event_type="participant_left",
    )
    db.commit()
    db.close()
    return {"ok": True}


@internal_bp.post("/meeting-ended")
def meeting_ended():
    payload = request.get_json(force=True)
    meeting_id = (payload.get("meeting_id") or "").strip()
    participant_role = (payload.get("participant_role") or "").strip().lower()
    ended_by_role = (payload.get("ended_by_role") or participant_role or "").strip().lower()
    ended_by_id = (payload.get("ended_by_id") or "").strip()
    if not meeting_id or participant_role not in {"host", "guest"}:
        return jsonify({"error": "meeting_id and valid participant_role required"}), 400

    db = get_db(current_app.config)
    meeting = get_scheduled_meeting(db, meeting_id)
    if not meeting:
        db.close()
        return jsonify({"error": "meeting_not_found"}), 404

    mark_meeting_participant_left(db, meeting_id=meeting_id, participant_role=participant_role)
    record_meeting_event(
        db,
        meeting_id=meeting_id,
        actor_type=participant_role,
        actor_id=ended_by_id or (meeting["consultant_id"] if participant_role == "host" else meeting["client_id"]),
        event_type="participant_left",
        details={"ended_by_role": ended_by_role},
    )
    if (
        participant_role == "host"
        and meeting["status"] in {"scheduled", "client_viewed", "accepted", "in_progress"}
        and _host_end_should_complete_meeting(meeting)
    ):
        attendance_outcome = "completed" if meeting["client_joined_at"] else "client_no_show"
        completed = complete_scheduled_meeting(
            db,
            meeting_id=meeting_id,
            attendance_outcome=attendance_outcome,
            ended_by_role=ended_by_role or "host",
            ended_by_id=ended_by_id or meeting["consultant_id"],
        )
        if completed:
            _ensure_next_weekly_occurrence(
                db,
                meeting_id=meeting_id,
                actor_type="system",
                actor_id="meeting-ended",
            )
            record_meeting_event(
                db,
                meeting_id=meeting_id,
                actor_type="system",
                actor_id="meeting-ended",
                event_type="meeting_completed",
                details={"attendance_outcome": attendance_outcome},
            )
    db.commit()
    db.close()
    return {"ok": True}


def _compute_baseline(storage: EncryptedStorage, db, client_id: str):
    rows = db.execute(
        """
        SELECT biomarker_storage_key
        FROM sessions
        WHERE client_id = ? AND biomarker_storage_key IS NOT NULL
        ORDER BY ended_at DESC, created_at DESC
        LIMIT 5
        """,
        (client_id,),
    ).fetchall()
    metrics: Dict[str, List[float]] = {}
    for row in rows:
        payload = storage.get_json(row["biomarker_storage_key"], client_id)
        if not payload:
            continue
        for key, value in payload.get("averages", {}).items():
            if isinstance(value, (int, float)):
                metrics.setdefault(key, []).append(float(value))
                continue
            if isinstance(value, dict):
                avg_value = value.get("avg")
                if isinstance(avg_value, (int, float)):
                    metrics.setdefault(key, []).append(float(avg_value))
    averages = {
        key: round(sum(values) / len(values), 4)
        for key, values in metrics.items()
        if values
    }
    return {
        "window_sessions": len(rows),
        "averages": averages,
    }


def _load_recent_summaries(storage: EncryptedStorage, db, client_id: str, limit: int = 5):
    rows = db.execute(
        """
        SELECT id, ended_at, started_at, created_at, summary_storage_key
        FROM sessions
        WHERE client_id = ? AND summary_storage_key IS NOT NULL
        ORDER BY COALESCE(ended_at, started_at, created_at) DESC
        LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()
    summaries = []
    for row in rows:
        payload = _normalize_summary_payload(storage.get_json(row["summary_storage_key"], client_id))
        if not payload:
            continue
        summaries.append(
            {
                "session_id": row["id"],
                "ended_at": row["ended_at"] or row["started_at"] or row["created_at"],
                "brief_overview": payload.get("brief_overview") or payload.get("overview") or "",
                "full_summary": payload.get("full_summary") or payload.get("overview") or "",
                "biomarker_summary": payload.get("biomarker_summary") or "",
                "risk_overview": payload.get("risk_overview") or "",
                "follow_up": payload.get("follow_up") or "",
            }
        )
    return summaries


def _normalize_summary_payload(summary):
    if isinstance(summary, str):
        brief_overview = summary
        full_summary = summary
        summary = {}
    elif isinstance(summary, dict):
        brief_overview = summary.get("brief_overview") or summary.get("overview") or ""
        full_summary = summary.get("full_summary") or summary.get("overview") or ""
    else:
        return None
    return {
        "brief_overview": brief_overview,
        "overview": brief_overview,
        "full_summary": full_summary,
        "biomarker_summary": summary.get("biomarker_summary") or "",
        "risk_overview": summary.get("risk_overview") or "",
        "follow_up": summary.get("follow_up") or "",
        "source": summary.get("source") or "custom-llm",
    }


@internal_bp.post("/session-complete")
def session_complete():
    payload = request.get_json(force=True)
    client_id = payload["client_id"]
    session_id = payload["session_id"]
    print(
        f"[consultant-dashboard] session-complete received "
        f"client_id={client_id} session_id={session_id} "
        f"profile={payload.get('profile', 'default')} urgent={bool(payload.get('urgent_escalation'))}"
    )
    storage = EncryptedStorage(storage_root_for_client(client_id), current_app.config["MASTER_KEY"])

    summary_key = None
    transcript_key = None
    biomarker_key = None
    meeting_id = payload.get("meeting_id")
    alert_keys = []
    if payload.get("summary"):
        summary_key = f"clients/{client_id}/sessions/{session_id}/summary.json.enc"
        storage.put_json(summary_key, client_id, _normalize_summary_payload(payload["summary"]))
    if payload.get("transcript"):
        transcript_key = f"clients/{client_id}/sessions/{session_id}/transcript.json.enc"
        storage.put_json(transcript_key, client_id, payload["transcript"])
    if payload.get("biomarkers"):
        biomarker_key = f"clients/{client_id}/sessions/{session_id}/biomarkers.json.enc"
        storage.put_json(biomarker_key, client_id, payload["biomarkers"])

    db = get_db(current_app.config)
    meeting_signal_flags = {
        "transcription_enabled": 0,
        "audio_biomarkers_enabled": 1,
        "video_biomarkers_enabled": 1,
    }
    if meeting_id:
        meeting_row = get_scheduled_meeting(db, meeting_id)
        if meeting_row:
            meeting_signal_flags = {
                "transcription_enabled": 1 if meeting_row["transcription_enabled"] else 0,
                "audio_biomarkers_enabled": 1 if meeting_row["audio_biomarkers_enabled"] else 0,
                "video_biomarkers_enabled": 1 if meeting_row["video_biomarkers_enabled"] else 0,
            }
    upsert_session(
        db,
        session_id=session_id,
        client_id=client_id,
        consultant_id=payload.get("consultant_id"),
        session_kind=payload.get("session_kind", "avatar_ai_session"),
        meeting_id=meeting_id,
        transcription_enabled=meeting_signal_flags["transcription_enabled"],
        audio_biomarkers_enabled=meeting_signal_flags["audio_biomarkers_enabled"],
        video_biomarkers_enabled=meeting_signal_flags["video_biomarkers_enabled"],
        profile_name=payload.get("profile", "default"),
        channel_name=payload.get("channel", ""),
        started_at=payload.get("started_at", ""),
        ended_at=payload.get("ended_at", ""),
        duration_seconds=int(payload.get("duration_seconds", 0)),
        status=payload.get("status", "completed"),
        summary_storage_key=summary_key,
        transcript_storage_key=transcript_key,
        biomarker_storage_key=biomarker_key,
        memory_storage_key=payload.get("memory_storage_key"),
        urgent_escalation=1 if payload.get("urgent_escalation") else 0,
        escalation_reason=payload.get("escalation_reason", ""),
    )
    if meeting_id:
        completed = complete_scheduled_meeting(
            db,
            meeting_id=meeting_id,
            linked_session_id=session_id,
            summary_storage_key=summary_key or "",
            biomarker_storage_key=biomarker_key or "",
            attendance_outcome=payload.get("attendance_outcome", ""),
            ended_by_role=payload.get("ended_by_role", ""),
            ended_by_id=payload.get("ended_by_id", ""),
        )
        if completed:
            _ensure_next_weekly_occurrence(
                db,
                meeting_id=meeting_id,
                actor_type="system",
                actor_id="session-complete",
            )
            record_meeting_event(
                db,
                meeting_id=meeting_id,
                actor_type="system",
                actor_id="session-complete",
                event_type="meeting_completed",
                details={"linked_session_id": session_id},
            )
    _refresh_client_derived_state(db, storage, client_id)
    baseline_key = db.execute(
        "SELECT baseline_storage_key FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()["baseline_storage_key"]
    if payload.get("urgent_escalation"):
        details_key = f"clients/{client_id}/sessions/{session_id}/alerts/urgent.json.enc"
        storage.put_json(
            details_key,
            client_id,
            {
                "reason": payload.get("escalation_reason", ""),
                "source": payload.get("escalation_source", "llm"),
            },
        )
        create_session_alert(
            db,
            session_id=session_id,
            client_id=client_id,
            severity="critical",
            source=payload.get("escalation_source", "llm"),
            title=payload.get("escalation_reason", "Urgent escalation triggered"),
            details_storage_key=details_key,
        )
        alert_keys.append(details_key)
    for index, alert in enumerate(payload.get("alerts", []), start=1):
        details = alert.get("details") or {}
        details_key = ""
        if details:
            details_key = f"clients/{client_id}/sessions/{session_id}/alerts/{index}.json.enc"
            storage.put_json(details_key, client_id, details)
            alert_keys.append(details_key)
        create_session_alert(
            db,
            session_id=session_id,
            client_id=client_id,
            severity=alert.get("severity", "info"),
            source=alert.get("source", "system"),
            title=alert.get("title", "Session alert"),
            details_storage_key=details_key,
        )
    log_audit(
        db,
        actor_type="system",
        actor_id="server-custom-llm",
        action="session_complete_ingested",
        target_type="session",
        target_id=session_id,
        session_id=session_id,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
        details={"client_id": client_id, "alert_count": len(alert_keys)},
    )
    db.commit()
    db.close()
    print(
        f"[consultant-dashboard] session-complete stored "
        f"client_id={client_id} session_id={session_id} baseline_key={baseline_key or 'none'}"
    )
    return {"ok": True, "session_id": session_id, "baseline_storage_key": baseline_key}


@internal_bp.post("/run-reminders")
def run_reminders():
    result = run_due_meeting_reminders()
    return jsonify(result)
