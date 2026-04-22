import base64
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, send_from_directory
from flask import jsonify

from .auth import require_admin, require_consultant
from .db import (
    cancel_scheduled_meeting,
    complete_scheduled_meeting,
    create_client_access_link,
    create_client_message,
    create_client,
    create_consultant,
    create_vendor,
    create_scheduled_meeting,
    delete_scheduled_meeting,
    deactivate_client,
    deactivate_consultant,
    delete_session,
    get_client_access_link_by_hash,
    get_client_detail,
    get_consultant_by_id,
    get_db,
    get_latest_session_artifacts,
    get_meeting_by_response_access_link_id,
    get_scheduled_meeting_detail,
    get_scheduled_meeting,
    get_session_detail,
    get_vendor_by_id,
    find_open_meeting_for_pair,
    get_client_access_link_by_id,
    list_meeting_events,
    list_active_meetings_for_reminders,
    list_meetings_for_client,
    list_recent_biomarker_keys,
    list_clients_for_consultant,
    list_client_messages,
    list_meetings_for_consultant,
    list_consultants,
    list_vendors,
    list_sessions,
    list_sessions_for_client,
    log_audit,
    mark_meeting_no_show,
    mark_client_access_link_used,
    mark_client_messages_read,
    record_meeting_event,
    update_meeting_invite_delivery,
    update_meeting_response_status,
    mark_meeting_participant_left,
    mark_meeting_reminder_sent,
    update_client,
    update_client_context,
    update_client_password,
    update_consultant,
    update_consultant_password,
    update_vendor,
    upsert_client_auth_identity,
)
from .client_identity import build_identity_hashes
from .messaging import (
    build_public_url,
    build_delivery_content,
    build_meeting_ics,
    build_meeting_response_link,
    build_reply_link,
    choose_delivery_channel,
    default_expiry,
    deliver_email,
    deliver_sms,
    hash_access_token,
    new_access_token,
)
from .meetings import (
    build_join_window,
    get_pair_channel,
    iso_utc,
    make_signed_join_bootstrap,
    make_signed_meeting_access_token,
    utc_now,
    verify_signed_meeting_access_token,
)
from .phone_numbers import country_options, infer_country_code, local_display_number, normalize_phone
from .realtime import publish_client_thread_update
from .storage import EncryptedStorage
from .vendors import current_branding, current_storage_root, current_vendor_slug, get_current_vendor, tenant_path, tenant_public_url, tenant_url_for
from werkzeug.security import generate_password_hash

web_bp = Blueprint("web", __name__)


def _brand_name() -> str:
    return current_branding().get("name") or current_app.config["BRAND_NAME"]


def _phone_form_value(phone_number: str):
    return {
        "country_code": infer_country_code(phone_number),
        "local_number": local_display_number(phone_number),
    }


def _parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _storage() -> EncryptedStorage:
    return EncryptedStorage(current_storage_root(), current_app.config["MASTER_KEY"])


def _client_app_base_url() -> str:
    configured = current_app.config["CLIENT_APP_URL"].rstrip("/")
    vendor_slug = current_vendor_slug()
    if configured.startswith("http://") or configured.startswith("https://"):
        if vendor_slug and "/v/" not in configured and configured.endswith("/app"):
            return f"{configured[:-4]}/v/{vendor_slug}/app"
        return configured
    return tenant_public_url("/app", vendor_slug)


def _rewrite_vendor_public_html(html: str) -> str:
    vendor_prefix = tenant_path("/")
    replacements = {
        'href="/consultant/login"': f'href="{tenant_path("/consultant/login")}"',
        'href="/admin/login"': f'href="{tenant_path("/admin/login")}"',
        'href="/app?profile=therapy&autoconnect=true&returnurl=/"': f'href="{tenant_path("/app?profile=therapy&autoconnect=true&returnurl=/")}"',
        '(isLocal ? "http://localhost:8084" : "/app")': f'(isLocal ? "http://localhost:8084" : "{tenant_path("/app")}")',
        '(isLocal ? "http://127.0.0.1:8090" : "")': f'(isLocal ? "http://127.0.0.1:8090" : "{tenant_path("")}")',
        'href="privacy.html"': f'href="{tenant_path("/privacy.html")}"',
        'href="terms.html"': f'href="{tenant_path("/terms.html")}"',
    }
    rewritten = html
    for source, target in replacements.items():
        rewritten = rewritten.replace(source, target)
    return rewritten.replace('href="css/', f'href="{vendor_prefix}css/').replace('src="img/', f'src="{vendor_prefix}img/')


def _serve_vendor_public_asset(asset_path: str):
    vendor = get_current_vendor()
    www_root = Path((vendor.get("www_root") or "").strip())
    if not www_root.exists() or not www_root.is_dir():
        abort(404)

    requested = (asset_path or "index.html").lstrip("/")
    target = www_root / requested
    if target.is_dir():
        target = target / "index.html"
    try:
        target = target.resolve()
    except FileNotFoundError:
        abort(404)
    if www_root.resolve() not in target.parents and target != www_root.resolve():
        abort(404)
    if not target.exists() or not target.is_file():
        abort(404)
    if target.suffix.lower() == ".html":
        body = _rewrite_vendor_public_html(target.read_text(encoding="utf-8"))
        return Response(body, mimetype="text/html")
    return send_from_directory(str(www_root), str(target.relative_to(www_root)))


def _client_profile_name() -> str:
    return current_app.config.get("CLIENT_PROFILE", "therapy").strip() or "therapy"


def _build_client_join_bootstrap(meeting_id: str, access_link_id: str) -> str:
    payload = {
        "meeting_id": meeting_id,
        "response_access_link_id": access_link_id,
        "participant_role": "guest",
        "exp": int(time.time()) + 300,
    }
    return make_signed_join_bootstrap(current_app.config["INTERNAL_SHARED_SECRET"], payload)


def _build_client_join_url(token: str) -> str:
    return tenant_url_for("web.meeting_response_join", token=token)


def _build_signed_meeting_response_token(link) -> str:
    expires_at = _parse_iso_datetime(link["expires_at"])
    exp = int(expires_at.timestamp()) if expires_at else int(time.time()) + (24 * 3600)
    return make_signed_meeting_access_token(
        current_app.config["INTERNAL_SHARED_SECRET"],
        link["id"],
        exp,
    )


def _resolve_meeting_access(db, token: str):
    link = get_client_access_link_by_hash(db, hash_access_token(token))
    if link:
        expires_at = _parse_iso_datetime(link["expires_at"])
        if expires_at and expires_at < datetime.now(timezone.utc):
            return None, None, 410
        meeting = get_meeting_by_response_access_link_id(db, link["id"])
        return link, meeting, None

    signed = verify_signed_meeting_access_token(current_app.config["INTERNAL_SHARED_SECRET"], token)
    if not signed:
        return None, None, 404
    link = get_client_access_link_by_id(db, signed.get("response_access_link_id", ""))
    if not link:
        return None, None, 404
    expires_at = _parse_iso_datetime(link["expires_at"])
    if expires_at and expires_at < datetime.now(timezone.utc):
        return None, None, 410
    meeting = get_meeting_by_response_access_link_id(db, link["id"])
    return link, meeting, None


def _build_ai_join_url(meeting_id: str = "") -> str:
    suffix = f"&scheduled_meeting_id={meeting_id}" if meeting_id else ""
    return f"{_client_app_base_url()}/?profile={_client_profile_name()}&autoconnect=true{suffix}"


def _default_transcription_provider(meeting_type: str) -> str:
    return "agora_stt" if (meeting_type or "").strip().lower() == "human" else ""


def _default_transcription_language() -> str:
    return "en-US"


def _build_consultant_join_bootstrap(meeting_id: str, consultant_id: str, channel_name: str) -> str:
    payload = {
        "meeting_id": meeting_id,
        "consultant_id": consultant_id,
        "channel_name": channel_name,
        "participant_role": "host",
        "exp": int(time.time()) + (8 * 60 * 60),
    }
    return make_signed_join_bootstrap(current_app.config["INTERNAL_SHARED_SECRET"], payload)


def _meeting_is_open_status(status: str) -> bool:
    return status in {"scheduled", "client_viewed", "accepted", "in_progress"}


def _meeting_type_display(meeting_type: str) -> str:
    return "AI" if (meeting_type or "").strip().lower() == "ai" else "Human"


def _meeting_field(meeting, key: str, default=""):
    if isinstance(meeting, dict):
        return meeting.get(key, default)
    try:
        return meeting[key]
    except Exception:
        return default


def _meeting_is_joinable_for_host(meeting, now: Optional[datetime] = None) -> bool:
    if not meeting or meeting["status"] not in {"scheduled", "client_viewed", "accepted", "in_progress"}:
        return False
    now = now or datetime.now(timezone.utc)
    join_start = _parse_iso_datetime(meeting["join_window_start_at"])
    join_end = _parse_iso_datetime(meeting["join_window_end_at"])
    if join_start and now < join_start:
        return False
    if join_end and now > join_end:
        return False
    return True


def _meeting_is_joinable_for_guest(meeting, now: Optional[datetime] = None) -> bool:
    if not meeting or meeting["status"] not in {"scheduled", "client_viewed", "accepted", "declined", "in_progress"}:
        return False
    now = now or datetime.now(timezone.utc)
    guest_join_start = _parse_iso_datetime(meeting["scheduled_start_at"])
    join_end = _parse_iso_datetime(meeting["join_window_end_at"])
    if guest_join_start:
        guest_join_start = guest_join_start - timedelta(minutes=10)
        if now < guest_join_start:
            return False
    if join_end and now > join_end:
        return False
    return True


def _meeting_is_window_expired(meeting, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    join_end = _parse_iso_datetime(meeting["join_window_end_at"])
    return bool(join_end and now > join_end)


def _meeting_is_stale_open(meeting, now: Optional[datetime] = None) -> bool:
    return _meeting_is_open_status(meeting["status"]) and _meeting_is_window_expired(meeting, now=now)


def _meeting_status_display(meeting) -> str:
    if _meeting_is_stale_open(meeting):
        return "Expired"
    status = _meeting_field(meeting, "status", "")
    if status in {"scheduled", "client_viewed"}:
        return "Invited"
    if status == "accepted":
        return "Accepted"
    if status == "cancelled":
        return "Cancelled"
    if status == "declined":
        return "Declined"
    if status == "in_progress":
        start_at = _parse_iso_datetime(_meeting_field(meeting, "scheduled_start_at", ""))
        consultant_joined_at = _parse_iso_datetime(_meeting_field(meeting, "consultant_joined_at", ""))
        client_joined_at = _parse_iso_datetime(_meeting_field(meeting, "client_joined_at", ""))
        now = datetime.now(timezone.utc)
        if start_at and now < start_at and consultant_joined_at and not client_joined_at:
            return "Ready"
        return "Meeting now"
    if status == "completed":
        return "Completed"
    return status.replace("_", " ").title()


def _decorate_meeting(meeting, now: Optional[datetime] = None):
    status_display = _meeting_status_display(meeting)
    stale_open = _meeting_is_stale_open(meeting, now=now)
    decorated = dict(meeting)
    decorated["status_display"] = status_display
    decorated["stale_open"] = stale_open
    decorated["is_live_display"] = status_display == "Meeting now"
    decorated["meeting_type_display"] = _meeting_type_display(_meeting_field(meeting, "meeting_type", "human"))
    decorated["repeat_weekly_display"] = bool(_meeting_field(meeting, "repeat_weekly", 0))
    decorated["transcription_enabled_display"] = bool(_meeting_field(meeting, "transcription_enabled", 0))
    decorated["audio_biomarkers_enabled_display"] = bool(_meeting_field(meeting, "audio_biomarkers_enabled", 1))
    decorated["video_biomarkers_enabled_display"] = bool(_meeting_field(meeting, "video_biomarkers_enabled", 1))
    return decorated


def _normalize_next_meeting_fields(row: dict) -> None:
    next_status = row.get("next_meeting_status")
    next_time = row.get("next_meeting_at")
    if next_status and next_time:
        pseudo_meeting = {
            "status": next_status,
            "scheduled_start_at": next_time,
            "scheduled_end_at": next_time,
            "join_window_end_at": next_time,
        }
        status_display = _meeting_status_display(pseudo_meeting)
        row["next_meeting_status_display"] = status_display
    else:
        row["next_meeting_status_display"] = ""


def _session_kind_display(session_kind: str) -> str:
    if session_kind == "consultant_live_session":
        return "Human"
    if session_kind == "avatar_ai_session":
        return "AI"
    return session_kind.replace("_", " ").title()


def _build_consultant_join_url(meeting_id: str, consultant_id: str, channel_name: str) -> str:
    bootstrap = _build_consultant_join_bootstrap(meeting_id, consultant_id, channel_name)
    return (
        f"{_client_app_base_url()}/?meeting_mode=true"
        f"&profile={_client_profile_name()}"
        f"&join_bootstrap={bootstrap}"
    )


def _meeting_type_join_label(meeting_type: str) -> str:
    return "Join AI Meeting" if (meeting_type or "").strip().lower() == "ai" else "Enter Meeting Room"


def _find_host_join_target(db, meeting, now: Optional[datetime] = None):
    if not meeting or (meeting["meeting_type"] or "human").strip().lower() == "ai":
        return meeting
    now = now or datetime.now(timezone.utc)
    if _meeting_is_joinable_for_host(meeting, now=now):
        return meeting
    pair_meetings = db.execute(
        """
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email,
               c.phone_number AS client_phone_number,
               co.name AS consultant_name, co.email AS consultant_email
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        WHERE sm.client_id = ?
          AND sm.consultant_id = ?
          AND sm.meeting_type = 'human'
          AND sm.id != ?
          AND sm.status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
        ORDER BY CASE WHEN sm.status = 'in_progress' THEN 0 ELSE 1 END,
                 CASE WHEN sm.status = 'accepted' THEN 1
                      WHEN sm.status = 'client_viewed' THEN 2
                      WHEN sm.status = 'scheduled' THEN 3
                      ELSE 4 END,
                 sm.scheduled_start_at DESC
        """,
        (meeting["client_id"], meeting["consultant_id"], meeting["id"]),
    ).fetchall()
    for candidate in pair_meetings:
        if _meeting_is_joinable_for_host(candidate, now=now):
            return candidate
    return meeting


def _find_guest_join_target(db, meeting, now: Optional[datetime] = None):
    if not meeting or (meeting["meeting_type"] or "human").strip().lower() == "ai":
        return meeting
    now = now or datetime.now(timezone.utc)
    if _meeting_is_joinable_for_guest(meeting, now=now):
        return meeting
    pair_meetings = db.execute(
        """
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email,
               c.phone_number AS client_phone_number,
               co.name AS consultant_name, co.email AS consultant_email
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        WHERE sm.client_id = ?
          AND sm.consultant_id = ?
          AND sm.meeting_type = 'human'
          AND sm.id != ?
          AND sm.status IN ('scheduled', 'client_viewed', 'accepted', 'declined', 'in_progress')
        ORDER BY CASE WHEN sm.status = 'in_progress' THEN 0 ELSE 1 END,
                 CASE WHEN sm.status = 'scheduled' THEN 1
                      WHEN sm.status = 'client_viewed' THEN 2
                      WHEN sm.status = 'accepted' THEN 3
                      WHEN sm.status = 'declined' THEN 4
                      ELSE 5 END,
                 sm.scheduled_start_at DESC
        """,
        (meeting["client_id"], meeting["consultant_id"], meeting["id"]),
    ).fetchall()
    for candidate in pair_meetings:
        if _meeting_is_joinable_for_guest(candidate, now=now):
            return candidate
    return meeting


def _render_meeting_launch_page(*, join_url: str, return_url: str, heading: str, detail: str):
    return render_template(
        "shared/meeting_launch.html",
        brand=_brand_name(),
        theme="consultant",
        title=f"{_brand_name()} | Opening Meeting",
        join_url=join_url,
        return_url=return_url,
        heading=heading,
        detail=detail,
    )


def _consultant_join_route(meeting_id: str) -> str:
    return tenant_url_for("web.consultant_meeting_join", meeting_id=meeting_id)


def _default_meeting_start_value(timezone_name: str) -> str:
    tz_name = timezone_name or "Europe/London"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    local_now = datetime.now(tz).replace(second=0, microsecond=0)
    return local_now.strftime("%Y-%m-%dT%H:%M")


def _parse_meeting_schedule_form(form_defaults: dict):
    timezone_name = form_defaults["timezone_name"] or "Europe/London"
    start_at = datetime.fromisoformat(form_defaults["scheduled_start_at"])
    if start_at.tzinfo is None:
        try:
            start_at = start_at.replace(tzinfo=ZoneInfo(timezone_name))
        except Exception:
            start_at = start_at.replace(tzinfo=timezone.utc)
    start_at = start_at.astimezone(timezone.utc)
    duration_minutes = int(form_defaults["duration_minutes"] or "30")
    if duration_minutes < 5 or duration_minutes > 240:
        raise ValueError("Meeting duration must be between 5 and 240 minutes.")
    end_at = start_at + timedelta(minutes=duration_minutes)
    return timezone_name, start_at, end_at


def _is_immediate_schedule(start_at: datetime) -> bool:
    return start_at <= (datetime.now(timezone.utc) + timedelta(minutes=1))


def _create_meeting_from_form(db, *, consultant_id: str, client_id: str, form_defaults: dict):
    timezone_name, start_at, end_at = _parse_meeting_schedule_form(form_defaults)
    meeting_type = (form_defaults.get("meeting_type") or "human").strip().lower()
    transcription_enabled = bool(form_defaults.get("transcription_enabled")) or meeting_type == "ai"
    audio_biomarkers_enabled = bool(form_defaults.get("audio_biomarkers_enabled"))
    video_biomarkers_enabled = bool(form_defaults.get("video_biomarkers_enabled"))
    transcription_provider = (form_defaults.get("transcription_provider") or "").strip() if transcription_enabled else ""
    transcription_language = (form_defaults.get("transcription_language") or "").strip() if transcription_enabled else ""
    default_title = f"{_brand_name()} AI session" if meeting_type == "ai" else f"{_brand_name()} session"
    existing_open = find_open_meeting_for_pair(
        db,
        consultant_id=consultant_id,
        client_id=client_id,
        meeting_type=meeting_type,
    )
    if existing_open:
        existing_join_end = _parse_iso_utc(existing_open["join_window_end_at"]) or _parse_iso_utc(existing_open["scheduled_end_at"])
        if existing_join_end and existing_join_end < utc_now():
            complete_scheduled_meeting(
                db,
                meeting_id=existing_open["id"],
                attendance_outcome=existing_open["attendance_outcome"] or "expired",
                ended_by_role=existing_open["ended_by_role"] or "system",
                ended_by_id=existing_open["ended_by_id"] or "",
            )
            existing_open = None
        if existing_open:
            if _is_immediate_schedule(start_at):
                return existing_open["id"], True
            raise ValueError("This client already has an active meeting. Complete or cancel it before scheduling another.")
    access_token = new_access_token()
    access_link_id = create_client_access_link(
        db,
        client_id=client_id,
        created_by=consultant_id,
        token_hash=hash_access_token(access_token),
        expires_at=default_expiry(hours=24 * 30),
    )
    join_window_start_at, join_window_end_at = build_join_window(start_at, end_at)
    meeting_id = create_scheduled_meeting(
        db,
        client_id=client_id,
        consultant_id=consultant_id,
        meeting_type=meeting_type,
        repeat_weekly=bool(form_defaults.get("repeat_weekly")),
        transcription_enabled=transcription_enabled,
        audio_biomarkers_enabled=audio_biomarkers_enabled,
        video_biomarkers_enabled=video_biomarkers_enabled,
        transcription_provider=transcription_provider,
        transcription_language=transcription_language,
        title=form_defaults["title"] or default_title,
        invite_message=form_defaults["invite_message"],
        timezone_name=timezone_name,
        scheduled_start_at=iso_utc(start_at),
        scheduled_end_at=iso_utc(end_at),
        join_window_start_at=iso_utc(join_window_start_at),
        join_window_end_at=iso_utc(join_window_end_at),
        channel_name=get_pair_channel(consultant_id, client_id, meeting_type),
        response_access_link_id=access_link_id,
    )
    meeting = get_scheduled_meeting_detail(db, meeting_id, consultant_id=consultant_id)
    delivery_status, delivery_error, _hosted_link = _send_meeting_invite(db, meeting, access_token)
    record_meeting_event(
        db,
        meeting_id=meeting_id,
        actor_type="consultant",
        actor_id=consultant_id,
        event_type="scheduled",
        details={"delivery_status": delivery_status, "delivery_error": delivery_error},
    )
    return meeting_id, False


def _refresh_meeting_invite_for_immediate_use(
    db,
    *,
    meeting_id: str,
    consultant_id: str,
    client_id: str,
    form_defaults: dict,
):
    timezone_name, start_at, end_at = _parse_meeting_schedule_form(form_defaults)
    meeting_type = (form_defaults.get("meeting_type") or "human").strip().lower()
    default_title = f"{_brand_name()} AI session" if meeting_type == "ai" else f"{_brand_name()} session"
    access_token = new_access_token()
    access_link_id = create_client_access_link(
        db,
        client_id=client_id,
        created_by=consultant_id,
        token_hash=hash_access_token(access_token),
        expires_at=default_expiry(hours=24 * 30),
    )
    join_window_start_at, join_window_end_at = build_join_window(start_at, end_at)
    db.execute(
        """
        UPDATE scheduled_meetings
        SET title = ?,
            invite_message = ?,
            timezone_name = ?,
            scheduled_start_at = ?,
            scheduled_end_at = ?,
            join_window_start_at = ?,
            join_window_end_at = ?,
            response_access_link_id = ?,
            invite_delivery_status = 'pending',
            invite_delivery_error = '',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            form_defaults["title"] or default_title,
            form_defaults["invite_message"],
            timezone_name,
            iso_utc(start_at),
            iso_utc(end_at),
            iso_utc(join_window_start_at),
            iso_utc(join_window_end_at),
            access_link_id,
            meeting_id,
        ),
    )
    meeting = get_scheduled_meeting_detail(db, meeting_id, consultant_id=consultant_id)
    _send_meeting_invite(db, meeting, access_token)
    record_meeting_event(
        db,
        meeting_id=meeting_id,
        actor_type="consultant",
        actor_id=consultant_id,
        event_type="invite_resent",
        details={"mode": "immediate_reuse"},
    )


def _message_preview_rows(db, client_id: str, consultant_id: str, limit: int = 20):
    rows = list_client_messages(db, client_id=client_id, consultant_id=consultant_id, limit=limit)
    return list(reversed(rows))


def _parse_iso_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _format_meeting_when_for_invite(meeting) -> str:
    start_label = _parse_iso_datetime(_meeting_field(meeting, "scheduled_start_at", ""))
    if not start_label:
        return _meeting_field(meeting, "scheduled_start_at", "")
    timezone_name = (_meeting_field(meeting, "timezone_name", "") or "UTC").strip()
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
        timezone_name = "UTC"
    local_dt = start_label.astimezone(tz)
    tz_abbrev = local_dt.tzname() or ""
    offset_delta = local_dt.utcoffset() or timedelta(0)
    total_minutes = int(offset_delta.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    abs_minutes = abs(total_minutes)
    offset_hours = abs_minutes // 60
    offset_minutes = abs_minutes % 60
    offset_text = f"UTC{sign}{offset_hours:02d}:{offset_minutes:02d}"
    if tz_abbrev and tz_abbrev.upper() != timezone_name.upper():
        return f"{local_dt.strftime('%d %b %Y, %H:%M')} {timezone_name} ({tz_abbrev}, {offset_text})"
    return f"{local_dt.strftime('%d %b %Y, %H:%M')} {timezone_name} ({offset_text})"


def _meeting_delivery_content(meeting, hosted_link: str, *, reminder_kind: str = ""):
    start_text = _format_meeting_when_for_invite(meeting)
    immediate = _is_immediate_meeting(meeting)
    meeting_type = (meeting["meeting_type"] or "human").strip().lower()
    if meeting_type == "ai":
        if immediate:
            body = (
                f"{_brand_name()} AI invites you to start a session now.\n\n"
                f"Title: {meeting['title']}\n"
                f"You can join the AI session any time using the meeting link below.\n"
            )
        else:
            body = (
                f"{_brand_name()} AI has invited you to an AI session.\n\n"
                f"When: {start_text}\n"
                f"Title: {meeting['title']}\n"
            )
    elif immediate:
        body = (
            f"{meeting['consultant_name']} invites you to meet now.\n\n"
            f"Title: {meeting['title']}\n"
            f"They are waiting in the meeting now.\n"
        )
    else:
        body = (
            f"{meeting['consultant_name']} has invited you to a meeting.\n\n"
            f"When: {start_text}\n"
            f"Title: {meeting['title']}\n"
        )
    if meeting["invite_message"]:
        body += f"\n{meeting['invite_message']}\n"
    if not immediate and bool(_meeting_field(meeting, "repeat_weekly", 0)):
        body += "\nRepeats: Weekly\n"
    if reminder_kind == "24h":
        body += "\nReminder: your meeting is within the next 24 hours.\n"
    elif reminder_kind == "1m":
        body += "\nReminder: it is time to join now.\n"
    return body


def _is_immediate_meeting(meeting) -> bool:
    start_at = _parse_iso_datetime(meeting["scheduled_start_at"])
    if not start_at:
        return False
    return start_at <= (datetime.now(timezone.utc) + timedelta(minutes=1))


def _send_meeting_invite(db, meeting, access_token: str = "", *, reminder_kind: str = ""):
    link = get_client_access_link_by_id(db, meeting["response_access_link_id"])
    response_token = access_token or (_build_signed_meeting_response_token(link) if link else "")
    hosted_link = build_meeting_response_link(current_app.config, response_token, vendor_slug=current_vendor_slug())
    direct_join_link = build_public_url(
        current_app.config,
        tenant_url_for("web.meeting_response_join", token=response_token),
        vendor_slug=current_vendor_slug(),
    )
    immediate = _is_immediate_meeting(meeting)
    consultant_name = meeting["consultant_name"] or _brand_name()
    meeting_type = (meeting["meeting_type"] or "human").strip().lower()
    if meeting_type == "ai":
        subject = (
            f"Join {_brand_name()} AI Now"
            if immediate
            else f"AI Meeting Invite from {_brand_name()} AI"
        )
    else:
        subject = (
            f"Meet {consultant_name} Now"
            if immediate
            else f"Meeting Invite from {consultant_name}"
        )
    if reminder_kind == "24h":
        subject = f"Reminder: {subject}"
    elif reminder_kind == "1m":
        if meeting_type == "ai":
            subject = f"Meet Now with {_brand_name().upper()} AI"
        else:
            subject = f"Meet Now with {consultant_name.upper()}"
    delivery_status = "not_sent"
    delivery_error = "no_client_delivery_channel"
    if meeting["client_email"]:
        plain_text = _meeting_delivery_content(meeting, hosted_link, reminder_kind=reminder_kind)
        cta_link = hosted_link
        if meeting_type == "ai":
            cta = "Join AI Meeting"
        else:
            cta = "Enter Meeting Room" if immediate else "Review and respond"
            if immediate or reminder_kind == "1m":
                cta_link = direct_join_link
        html_body = (
            f"<p>{plain_text.replace(chr(10), '<br>')}</p>"
            f"<p><a href=\"{cta_link}\">{cta}</a></p>"
            f"<p>Sent by {_brand_name()}.</p>"
        )
        attachments = None
        if not immediate and meeting_type == "human":
            ics = build_meeting_ics(
                uid=f"{meeting['id']}@mindfix.me",
                title=meeting["title"],
                description=_meeting_delivery_content(meeting, hosted_link, reminder_kind=reminder_kind),
                start_at=meeting["scheduled_start_at"],
                end_at=meeting["scheduled_end_at"],
                hosted_url=hosted_link,
                organizer_email=meeting["consultant_email"] or current_app.config["EMAIL_FROM"],
                attendee_email=meeting["client_email"] or meeting["client_notification_email"] or "",
            )
            attachments = [
                {
                    "content": base64.b64encode(ics.encode("utf-8")).decode("ascii"),
                    "filename": "mindfix-meeting.ics",
                    "type": "text/calendar",
                    "disposition": "attachment",
                }
            ]
        delivery_status, delivery_error = deliver_email(
            current_app.config,
            to_email=meeting["client_email"],
            subject=subject,
            body=plain_text,
            reply_link=cta_link,
            kind="meeting_invite",
            html_override=html_body,
            plain_text_override=plain_text,
            attachments=attachments,
        )
    elif meeting["client_phone_number"]:
        sms_link = direct_join_link if meeting_type == "human" and (immediate or reminder_kind == "1m") else hosted_link
        delivery_status, delivery_error = deliver_sms(
            current_app.config,
            to_phone=meeting["client_phone_number"],
            body=f"You have a {_brand_name()} {'meet now' if immediate else 'meeting invite'}.",
            reply_link=sms_link,
        )
    update_meeting_invite_delivery(
        db,
        meeting_id=meeting["id"],
        delivery_status=delivery_status,
        delivery_error=delivery_error,
    )
    record_meeting_event(
        db,
        meeting_id=meeting["id"],
        actor_type="system",
        actor_id="invite-delivery",
        event_type=("invite_sent" if delivery_status == "sent" else "invite_failed") if not reminder_kind else ("reminder_sent" if delivery_status == "sent" else "reminder_failed"),
        details={"delivery_status": delivery_status, "delivery_error": delivery_error, "reminder_kind": reminder_kind},
    )
    return delivery_status, delivery_error, hosted_link


def run_due_meeting_reminders():
    db = get_db(current_app.config)
    now = datetime.now(timezone.utc)
    sent = {"24h": 0, "1m": 0}
    failed = {"24h": 0, "1m": 0}
    skipped = 0

    for meeting in list_active_meetings_for_reminders(db):
        start_at = _parse_iso_datetime(meeting["scheduled_start_at"])
        created_at = _parse_iso_datetime(meeting["created_at"])
        if not start_at or not created_at:
            continue
        # Skip immediate/meet-now invites; reminders are only for scheduled meetings.
        if start_at <= (created_at + timedelta(minutes=2)):
            skipped += 1
            continue

        reminder_kind = ""
        if not meeting["reminder_24h_sent_at"] and now >= (start_at - timedelta(hours=24)) and now < start_at:
            reminder_kind = "24h"
        elif not meeting["reminder_1m_sent_at"] and now >= (start_at - timedelta(minutes=1)) and now <= (start_at + timedelta(minutes=5)):
            reminder_kind = "1m"
        if not reminder_kind:
            continue

        delivery_status, delivery_error, _ = _send_meeting_invite(db, meeting, reminder_kind=reminder_kind)
        if delivery_status == "sent" and mark_meeting_reminder_sent(db, meeting_id=meeting["id"], reminder_kind=reminder_kind):
            sent[reminder_kind] += 1
        else:
            failed[reminder_kind] += 1
            record_meeting_event(
                db,
                meeting_id=meeting["id"],
                actor_type="system",
                actor_id="reminder-runner",
                event_type="reminder_failed",
                details={"delivery_status": delivery_status, "delivery_error": delivery_error, "reminder_kind": reminder_kind},
            )

    db.commit()
    db.close()
    return {
        "ok": True,
        "sent_24h": sent["24h"],
        "sent_1m": sent["1m"],
        "failed_24h": failed["24h"],
        "failed_1m": failed["1m"],
        "skipped_immediate": skipped,
    }


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
    reply_link = build_reply_link(current_app.config, token, vendor_slug=current_vendor_slug())
    delivery_channel = choose_delivery_channel(
        client_email=client["email"] or "",
        client_phone=client["phone_number"] or "",
    )
    _subject, outbound_body = build_delivery_content(
        channel=delivery_channel if delivery_channel != "portal" else "email",
        brand=_brand_name(),
        client_name=client["display_name"],
        body=message_body,
    )
    if delivery_channel == "email":
        delivery_status, delivery_error = deliver_email(
            current_app.config,
            to_email=client["email"] or "",
            subject=f"{_brand_name()} message",
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
        metadata={"delivery_kind": delivery_channel},
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
                continue
            if isinstance(value, dict):
                avg_value = value.get("avg")
                if isinstance(avg_value, (int, float)):
                    metrics.setdefault(key, []).append(float(avg_value))

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
        "unread_messages": db.execute(
            """
            SELECT COUNT(*) AS c
            FROM client_messages m
            WHERE m.consultant_id = ?
              AND m.direction = 'inbound'
              AND m.read_by_consultant_at IS NULL
            """,
            (consultant_id,),
        ).fetchone()["c"],
        "avg_ai_duration_seconds": db.execute(
            """
            SELECT CAST(AVG(duration_seconds) AS INTEGER) AS avg_duration
            FROM sessions
            WHERE consultant_id = ?
              AND session_kind = 'avatar_ai_session'
              AND duration_seconds IS NOT NULL
              AND duration_seconds > 0
            """,
            (consultant_id,),
        ).fetchone()["avg_duration"],
        "avg_human_duration_seconds": db.execute(
            """
            SELECT CAST(AVG(duration_seconds) AS INTEGER) AS avg_duration
            FROM sessions
            WHERE consultant_id = ?
              AND session_kind = 'consultant_live_session'
              AND duration_seconds IS NOT NULL
              AND duration_seconds > 0
            """,
            (consultant_id,),
        ).fetchone()["avg_duration"],
    }


def _format_duration_stat(duration_seconds):
    if not duration_seconds or duration_seconds <= 0:
        return "—"
    minutes, seconds = divmod(int(duration_seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
    if seconds == 0:
        return f"{minutes}m"
    return f"{minutes}m {seconds}s"


@web_bp.get("/home")
def home():
    return render_template("shared/home.html", brand=_brand_name())


@web_bp.get("/consultant/dashboard")
@require_consultant
def consultant_dashboard():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    consultant = get_consultant_by_id(db, consultant_id)
    stats = _consultant_dashboard_stats(db, consultant_id)
    db.close()
    stats["avg_ai_duration_display"] = _format_duration_stat(stats.get("avg_ai_duration_seconds"))
    stats["avg_human_duration_display"] = _format_duration_stat(stats.get("avg_human_duration_seconds"))
    return render_template(
        "consultant/dashboard.html",
        brand=_brand_name(),
        theme="consultant",
        stats=stats,
        consultant=consultant,
    )


@web_bp.route("/consultant/clients", methods=["GET", "POST"])
@require_consultant
def consultant_clients():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    clients = list_clients_for_consultant(db, consultant_id)
    db.close()
    decorated_clients = []
    for client in clients:
        row = dict(client)
        _normalize_next_meeting_fields(row)
        decorated_clients.append(row)
    return render_template(
        "consultant/clients.html",
        brand=_brand_name(),
        theme="consultant",
        clients=decorated_clients,
    )


def _render_consultant_client_new_form(*, form_defaults=None):
    form_defaults = form_defaults or {
        "phone_country_code": "US",
        "escalation_phone_country_code": "US",
    }
    return render_template(
        "consultant/client_new.html",
        brand=_brand_name(),
        theme="consultant",
        phone_countries=country_options(),
        form_defaults=form_defaults,
    )


@web_bp.route("/consultant/clients/new", methods=["GET", "POST"])
@require_consultant
def consultant_client_new():
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
            if initial_password and (not email or not raw_phone_number):
                flash("Email and phone number are required when setting a client password.", "error")
                db.close()
                return _render_consultant_client_new_form(form_defaults=form_defaults)
            if initial_password and len(initial_password) < 8:
                flash("Initial password must be at least 8 characters.", "error")
                db.close()
                return _render_consultant_client_new_form(form_defaults=form_defaults)
            try:
                phone_number = normalize_phone(raw_phone_number, phone_country_code) if raw_phone_number else ""
                escalation_phone_number = (
                    normalize_phone(raw_escalation_phone, escalation_phone_country_code)
                    if raw_escalation_phone
                    else phone_number
                )
            except ValueError as exc:
                flash(str(exc), "error")
                db.close()
                return _render_consultant_client_new_form(form_defaults=form_defaults)
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
            return redirect(tenant_url_for("web.consultant_client_detail", client_id=client_id))
    db.close()
    return _render_consultant_client_new_form(form_defaults=form_defaults)


@web_bp.route("/consultant/clients/<client_id>", methods=["GET", "POST"])
@require_consultant
def consultant_client_detail(client_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    if not client:
        db.close()
        abort(404)
    mark_client_messages_read(
        db,
        client_id=client_id,
        reader="consultant",
        consultant_id=consultant_id,
    )
    db.commit()

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
    meetings = list_meetings_for_client(db, client_id=client_id, consultant_id=consultant_id, limit=20)
    now = datetime.now(timezone.utc)
    meetings = [_decorate_meeting(meeting, now=now) for meeting in meetings]
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
        brand=_brand_name(),
        theme="consultant",
        client=client,
        sessions=sessions,
        meetings=meetings,
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


def _consultant_meeting_new_page(*, preselected_client_id: str = ""):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    clients = list_clients_for_consultant(db, consultant_id)
    selected_client_id = (request.values.get("client_id", "") or preselected_client_id or "").strip()
    client = get_client_detail(db, selected_client_id, consultant_id=consultant_id) if selected_client_id else None
    if selected_client_id and not client:
        db.close()
        abort(404)

    form_defaults = {
        "client_id": selected_client_id,
        "title": f"{_brand_name()} session",
        "meeting_type": "human",
        "repeat_weekly": False,
        "transcription_enabled": True,
        "audio_biomarkers_enabled": True,
        "video_biomarkers_enabled": True,
        "transcription_provider": _default_transcription_provider("human"),
        "transcription_language": _default_transcription_language(),
        "timezone_name": "Europe/London",
        "duration_minutes": "30",
        "invite_message": "",
        "scheduled_start_at": _default_meeting_start_value("Europe/London"),
    }
    if request.method == "POST":
        audio_biomarkers_enabled = (
            "1" in request.form.getlist("audio_biomarkers_enabled")
            if "audio_biomarkers_enabled" in request.form
            else True
        )
        video_biomarkers_enabled = (
            "1" in request.form.getlist("video_biomarkers_enabled")
            if "video_biomarkers_enabled" in request.form
            else True
        )
        transcription_enabled = (
            "1" in request.form.getlist("transcription_enabled")
            if "transcription_enabled" in request.form
            else True
        )
        form_defaults.update({
            "client_id": request.form.get("client_id", "").strip() or selected_client_id,
            "title": request.form.get("title", "").strip(),
            "meeting_type": (request.form.get("meeting_type", "human").strip() or "human").lower(),
            "repeat_weekly": request.form.get("repeat_weekly") == "1",
            "transcription_enabled": transcription_enabled,
            "audio_biomarkers_enabled": audio_biomarkers_enabled,
            "video_biomarkers_enabled": video_biomarkers_enabled,
            "transcription_provider": request.form.get("transcription_provider", "").strip() or _default_transcription_provider("human"),
            "transcription_language": request.form.get("transcription_language", "").strip() or _default_transcription_language(),
            "timezone_name": request.form.get("timezone_name", "").strip() or "Europe/London",
            "duration_minutes": request.form.get("duration_minutes", "30").strip(),
            "invite_message": request.form.get("invite_message", "").strip(),
            "scheduled_start_at": request.form.get("scheduled_start_at", "").strip(),
        })
        if form_defaults["meeting_type"] == "ai":
            form_defaults["transcription_enabled"] = True
            form_defaults["transcription_provider"] = request.form.get("transcription_provider", "").strip() or _default_transcription_provider("human")
            form_defaults["transcription_language"] = request.form.get("transcription_language", "").strip() or _default_transcription_language()
        try:
            selected_client_id = form_defaults["client_id"].strip()
            if not selected_client_id:
                raise ValueError("Select a client before scheduling the meeting.")
            client = get_client_detail(db, selected_client_id, consultant_id=consultant_id)
            if not client:
                raise ValueError("Selected client was not found.")
            current_app.logger.info(
                "meeting_form_submit consultant_id=%s client_id=%s type=%s start=%s repeat_weekly=%s stt=%s audio=%s video=%s",
                consultant_id,
                selected_client_id,
                form_defaults.get("meeting_type"),
                form_defaults.get("scheduled_start_at"),
                form_defaults.get("repeat_weekly"),
                form_defaults.get("transcription_enabled"),
                form_defaults.get("audio_biomarkers_enabled"),
                form_defaults.get("video_biomarkers_enabled"),
            )
            _timezone_name, requested_start_at, _requested_end_at = _parse_meeting_schedule_form(form_defaults)
            immediate_request = _is_immediate_schedule(requested_start_at)
            meeting_id, reused_existing = _create_meeting_from_form(
                db,
                consultant_id=consultant_id,
                client_id=selected_client_id,
                form_defaults=form_defaults,
            )
            if not reused_existing:
                log_audit(
                    db,
                    actor_type="consultant",
                    actor_id=consultant_id,
                    action="meeting_scheduled",
                    target_type="meeting",
                    target_id=meeting_id,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    user_agent=request.headers.get("User-Agent", ""),
                    details={"client_id": selected_client_id},
                )
            db.commit()
            db.close()
            if reused_existing:
                current_app.logger.info(
                    "meeting_form_reused consultant_id=%s client_id=%s meeting_id=%s immediate=%s",
                    consultant_id,
                    selected_client_id,
                    meeting_id,
                    immediate_request,
                )
                if immediate_request:
                    db = get_db(current_app.config)
                    try:
                        _refresh_meeting_invite_for_immediate_use(
                            db,
                            meeting_id=meeting_id,
                            consultant_id=consultant_id,
                            client_id=selected_client_id,
                            form_defaults=form_defaults,
                        )
                        db.commit()
                    except Exception:
                        db.rollback()
                        raise
                    finally:
                        db.close()
                flash("This client already has an active meeting. Opening it now.", "muted")
                if immediate_request and form_defaults.get("meeting_type") != "ai":
                    launch_db = get_db(current_app.config)
                    meeting = get_scheduled_meeting(launch_db, meeting_id)
                    launch_db.close()
                    if meeting:
                        join_url = _build_consultant_join_url(meeting_id, consultant_id, meeting["channel_name"])
                        return _render_meeting_launch_page(
                            join_url=join_url,
                            return_url=tenant_url_for("web.consultant_meeting_detail", meeting_id=meeting_id),
                            heading="Opening meeting",
                            detail="The meeting is opening in a new tab. Stay on this page to manage the meeting.",
                        )
                return redirect(tenant_url_for("web.consultant_meeting_detail", meeting_id=meeting_id))
            current_app.logger.info(
                "meeting_form_created consultant_id=%s client_id=%s meeting_id=%s immediate=%s",
                consultant_id,
                selected_client_id,
                meeting_id,
                immediate_request,
            )
            if immediate_request and form_defaults.get("meeting_type") != "ai":
                db = get_db(current_app.config)
                meeting = get_scheduled_meeting(db, meeting_id)
                db.close()
                flash("Meeting invite sent. Opening meeting now.", "muted")
                return _render_meeting_launch_page(
                    join_url=_build_consultant_join_url(meeting_id, consultant_id, meeting["channel_name"]),
                    return_url=tenant_url_for("web.consultant_meeting_detail", meeting_id=meeting_id),
                    heading="Opening meeting",
                    detail="The meeting is opening in a new tab. Stay here to manage the invite and meeting details.",
                )
            flash("Meeting scheduled", "muted")
            return redirect(tenant_url_for("web.consultant_meeting_detail", meeting_id=meeting_id))
        except ValueError as exc:
            db.rollback()
            flash(str(exc), "error")

    db.close()
    return render_template(
        "consultant/meeting_new.html",
        brand=_brand_name(),
        theme="consultant",
        client=client,
        clients=clients,
        form_defaults=form_defaults,
    )


@web_bp.route("/consultant/meetings/new", methods=["GET", "POST"])
@require_consultant
def consultant_meeting_new():
    return _consultant_meeting_new_page()


@web_bp.route("/consultant/clients/<client_id>/meetings/new", methods=["GET", "POST"])
@require_consultant
def consultant_client_meeting_new(client_id: str):
    return _consultant_meeting_new_page(preselected_client_id=client_id)


@web_bp.get("/consultant/meetings")
@require_consultant
def consultant_meetings():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    meetings = list_meetings_for_consultant(db, consultant_id=consultant_id, limit=100)
    db.close()
    filter_name = (request.args.get("filter") or "all").strip().lower()
    search = (request.args.get("q") or "").strip().lower()
    now = datetime.now(timezone.utc)
    meetings = [_decorate_meeting(meeting, now=now) for meeting in meetings]

    def include(meeting):
        status = meeting["status"]
        start_at = _parse_iso_datetime(meeting["scheduled_start_at"])
        if filter_name == "open":
            return status in {"scheduled", "client_viewed", "accepted", "in_progress"} and not meeting.get("stale_open")
        if filter_name == "upcoming":
            return status in {"scheduled", "client_viewed", "accepted"} and start_at and start_at > now
        if filter_name == "past":
            return status == "completed" or meeting.get("stale_open")
        if filter_name == "cancelled":
            return status in {"cancelled", "declined"}
        return True

    meetings = [meeting for meeting in meetings if include(meeting)]
    if search:
        filtered = []
        for meeting in meetings:
            haystack = " ".join(
                [
                    meeting["client_name"] or "",
                    meeting["title"] or "",
                    meeting["status"] or "",
                    meeting["scheduled_start_at"] or "",
                ]
            ).lower()
            if search in haystack:
                filtered.append(meeting)
        meetings = filtered
    return render_template(
        "consultant/meetings.html",
        brand=_brand_name(),
        theme="consultant",
        meetings=meetings,
        filter_name=filter_name,
        search=search,
    )


@web_bp.route("/consultant/meetings/<meeting_id>", methods=["GET", "POST"])
@require_consultant
def consultant_meeting_detail(meeting_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    meeting = get_scheduled_meeting_detail(db, meeting_id, consultant_id=consultant_id)
    if not meeting:
        db.close()
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "cancel":
            if cancel_scheduled_meeting(db, meeting_id=meeting_id):
                record_meeting_event(db, meeting_id=meeting_id, actor_type="consultant", actor_id=consultant_id, event_type="cancelled")
                log_audit(
                    db,
                    actor_type="consultant",
                    actor_id=consultant_id,
                    action="meeting_cancelled",
                    target_type="meeting",
                    target_id=meeting_id,
                )
                db.commit()
                flash("Meeting cancelled", "muted")
            else:
                flash("This meeting can no longer be cancelled.", "error")
        elif action == "resend_invite":
            reopened_declined = meeting["status"] == "declined"
            access_token = new_access_token()
            access_link_id = create_client_access_link(
                db,
                client_id=meeting["client_id"],
                created_by=consultant_id,
                token_hash=hash_access_token(access_token),
                expires_at=default_expiry(hours=24 * 30),
            )
            db.execute(
                "UPDATE scheduled_meetings SET response_access_link_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (access_link_id, meeting_id),
            )
            if reopened_declined:
                db.execute(
                    """
                    UPDATE scheduled_meetings
                    SET status = 'scheduled',
                        declined_at = NULL,
                        accepted_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (meeting_id,),
                )
            meeting = get_scheduled_meeting_detail(db, meeting_id, consultant_id=consultant_id)
            _send_meeting_invite(db, meeting, access_token)
            record_meeting_event(db, meeting_id=meeting_id, actor_type="consultant", actor_id=consultant_id, event_type="invite_resent")
            db.commit()
            if reopened_declined:
                flash("Invite resent. Meeting reopened for response.", "muted")
            else:
                flash("Invite resent", "muted")
        elif action == "mark_no_show":
            outcome = request.form.get("attendance_outcome", "client_no_show").strip()
            if mark_meeting_no_show(db, meeting_id=meeting_id, attendance_outcome=outcome):
                record_meeting_event(
                    db,
                    meeting_id=meeting_id,
                    actor_type="consultant",
                    actor_id=consultant_id,
                    event_type="no_show_marked",
                    details={"attendance_outcome": outcome},
                )
                db.commit()
                flash("No-show recorded", "muted")
            else:
                flash("This meeting can no longer be marked as a no-show.", "error")
        elif action == "delete":
            flash("Meeting deletion is disabled. Cancel the meeting instead so invite links remain valid.", "error")
        meeting = get_scheduled_meeting_detail(db, meeting_id, consultant_id=consultant_id)

    events = list_meeting_events(db, meeting_id)
    linked_summary = None
    linked_biomarkers = None
    storage = _storage()
    if meeting["summary_storage_key"]:
        linked_summary = storage.get_json(meeting["summary_storage_key"], meeting["client_id"])
    if meeting["biomarker_storage_key"]:
        linked_biomarkers = storage.get_json(meeting["biomarker_storage_key"], meeting["client_id"])
    biomarker_headlines = _build_latest_biomarker_highlights(linked_biomarkers)
    biomarker_sections = _grouped_biomarker_sections(linked_biomarkers)
    now = datetime.now(timezone.utc)
    meeting = _decorate_meeting(meeting, now=now)
    scheduled_start_at = _parse_iso_datetime(meeting["scheduled_start_at"])
    is_now_meeting = bool(scheduled_start_at and scheduled_start_at <= now and meeting["status"] not in {"completed", "cancelled", "declined"})
    is_ai_meeting = (meeting["meeting_type"] or "human").strip().lower() == "ai"
    join_url = _consultant_join_route(meeting_id)
    can_cancel = meeting["status"] in {"scheduled", "client_viewed", "accepted"}
    can_end = meeting["status"] == "in_progress"
    can_delete = False
    can_resend_invite = bool(scheduled_start_at and scheduled_start_at > now and meeting["status"] not in {"completed", "cancelled"})
    db.close()
    return render_template(
        "consultant/meeting_detail.html",
        brand=_brand_name(),
        theme="consultant",
        meeting=meeting,
        events=events,
        join_url=join_url,
        linked_summary=linked_summary,
        biomarker_headlines=biomarker_headlines,
        biomarker_sections=biomarker_sections,
        can_cancel=can_cancel,
        can_end=can_end,
        can_delete=can_delete,
        can_resend_invite=can_resend_invite,
        is_now_meeting=is_now_meeting,
        is_ai_meeting=is_ai_meeting,
        meeting_type_display=_meeting_type_display(meeting["meeting_type"]),
        join_label=_meeting_type_join_label(meeting["meeting_type"]),
        status_display=_meeting_status_display(meeting),
    )


@web_bp.get("/consultant/meetings/<meeting_id>/join")
@require_consultant
def consultant_meeting_join(meeting_id: str):
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    meeting = get_scheduled_meeting_detail(db, meeting_id, consultant_id=consultant_id)
    if not meeting:
        db.close()
        abort(404)
    if (meeting["meeting_type"] or "human").strip().lower() == "ai":
        db.close()
        flash("AI meetings are joined from the client invite link.", "error")
        return redirect(tenant_url_for("web.consultant_meeting_detail", meeting_id=meeting_id))
    target_meeting = _find_host_join_target(db, meeting, now=utc_now())
    db.close()
    return redirect(_build_consultant_join_url(target_meeting["id"], consultant_id, target_meeting["channel_name"]))


@web_bp.get("/meetings/respond/<token>/join")
def meeting_response_join(token: str):
    db = get_db(current_app.config)
    link, meeting, error_status = _resolve_meeting_access(db, token)
    if error_status == 404:
        db.close()
        abort(404)
    if error_status == 410:
        db.close()
        abort(410)
    if not meeting:
        db.close()
        abort(404)
    target_meeting = _find_guest_join_target(db, meeting, now=utc_now())
    db.close()
    if (target_meeting["meeting_type"] or "human").strip().lower() == "ai":
        join_url = _build_ai_join_url(target_meeting["id"])
    else:
        join_url = (
            f"{_client_app_base_url()}/?meeting_mode=true"
            f"&profile={_client_profile_name()}"
            f"&join_bootstrap={_build_client_join_bootstrap(target_meeting['id'], target_meeting['response_access_link_id'])}"
        )
    return redirect(join_url)


@web_bp.get("/meetings/respond/<token>/invite.ics")
def meeting_response_ics(token: str):
    db = get_db(current_app.config)
    link, meeting, error_status = _resolve_meeting_access(db, token)
    if error_status == 404:
        db.close()
        abort(404)
    if error_status == 410:
        db.close()
        abort(410)
    db.close()
    if not meeting:
        abort(404)
    hosted_link = build_meeting_response_link(current_app.config, token, vendor_slug=current_vendor_slug())
    ics = build_meeting_ics(
        uid=f"{meeting['id']}@mindfix.me",
        title=meeting["title"],
        description=_meeting_delivery_content(meeting, hosted_link),
        start_at=meeting["scheduled_start_at"],
        end_at=meeting["scheduled_end_at"],
        hosted_url=hosted_link,
        organizer_email=meeting["consultant_email"] or current_app.config["EMAIL_FROM"],
        attendee_email=meeting["client_email"] or meeting["client_notification_email"] or "",
    )
    return Response(
        ics,
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{meeting["id"]}.ics"'},
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
            delivery_status, delivery_error = _send_client_message(
                db,
                consultant_id=consultant_id,
                client=client,
                client_id=client_id,
                message_body=form_defaults["body"],
            )
            db.commit()
            db.close()
            if delivery_status == "sent":
                flash("Message sent", "muted")
            elif delivery_status == "not_sent":
                flash("Message saved, but delivery is not configured yet", "muted")
            else:
                flash("Message saved, but delivery failed", "error")
            return redirect(tenant_url_for("web.consultant_client_detail", client_id=client_id))

    messages = _message_preview_rows(db, client_id, consultant_id, limit=20)
    db.close()
    return render_template(
        "consultant/message_compose.html",
        brand=_brand_name(),
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
            brand=_brand_name(),
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
        brand=_brand_name(),
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


@web_bp.route("/meetings/respond/<token>", methods=["GET", "POST"])
def meeting_response_page(token: str):
    db = get_db(current_app.config)
    link, meeting, error_status = _resolve_meeting_access(db, token)
    if error_status == 404:
        db.close()
        abort(404)
    if error_status == 410:
        db.close()
        return render_template(
            "shared/meeting_response.html",
            brand=_brand_name(),
            expired=True,
            meeting=None,
            join_url="",
        ), 410
    if not meeting:
        db.close()
        abort(404)

    if meeting["status"] == "scheduled":
        db.execute(
            "UPDATE scheduled_meetings SET status = 'client_viewed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (meeting["id"],),
        )
        record_meeting_event(
            db,
            meeting_id=meeting["id"],
            actor_type="guest",
            actor_id=meeting["client_id"],
            event_type="client_viewed",
        )
        db.commit()
        meeting = get_meeting_by_response_access_link_id(db, link["id"])

    is_ai_meeting = (meeting["meeting_type"] or "human").strip().lower() == "ai"

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action in {"accept", "accept_join"}:
            if update_meeting_response_status(db, meeting_id=meeting["id"], status="accepted"):
                record_meeting_event(db, meeting_id=meeting["id"], actor_type="guest", actor_id=meeting["client_id"], event_type="accepted")
                db.commit()
                if action == "accept_join":
                    return _render_meeting_launch_page(
                        join_url=tenant_url_for("web.meeting_response_join", token=token),
                        return_url=tenant_url_for("web.meeting_response_page", token=token),
                        heading="Opening meeting",
                        detail="The meeting is opening in a new tab. You can return here if you need the invite details again.",
                    )
                flash("Meeting accepted", "muted")
            else:
                flash("This meeting can no longer be accepted.", "error")
        elif action == "decline":
            if update_meeting_response_status(db, meeting_id=meeting["id"], status="declined"):
                record_meeting_event(db, meeting_id=meeting["id"], actor_type="guest", actor_id=meeting["client_id"], event_type="declined")
                db.commit()
                flash("Meeting declined", "muted")
            else:
                flash("This meeting can no longer be declined.", "error")
        meeting = get_meeting_by_response_access_link_id(db, link["id"])

    now = utc_now()
    join_end = _parse_iso_datetime(meeting["join_window_end_at"])
    target_meeting = _find_guest_join_target(db, meeting, now=now)
    guest_joinable = (not is_ai_meeting) and _meeting_is_joinable_for_guest(target_meeting, now=now)
    guest_join_start = _parse_iso_datetime(meeting["scheduled_start_at"])
    if guest_join_start:
        guest_join_start = guest_join_start - timedelta(minutes=10)
    join_url = _build_client_join_url(token) if not is_ai_meeting else _build_ai_join_url(target_meeting["id"])
    add_to_calendar_url = ""
    if not is_ai_meeting and not _is_immediate_meeting(meeting):
        add_to_calendar_url = tenant_url_for("web.meeting_response_ics", token=token)
    status_display = _meeting_status_display(meeting)
    db.close()
    return render_template(
        "shared/meeting_response.html",
        brand=_brand_name(),
        expired=False,
        meeting=meeting,
        join_url=join_url,
        add_to_calendar_url=add_to_calendar_url,
        join_label=_meeting_type_join_label(meeting["meeting_type"]),
        meeting_type_display=_meeting_type_display(meeting["meeting_type"]),
        is_ai_meeting=is_ai_meeting,
        status_display=status_display,
    )


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
    return redirect(tenant_url_for("web.consultant_clients"))


@web_bp.get("/consultant/sessions")
@require_consultant
def consultant_sessions():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    clients = list_clients_for_consultant(db, consultant_id=consultant_id)
    sessions = list_sessions(db, consultant_id=consultant_id, limit=100)
    storage = _storage()
    selected_client_id = (request.args.get("client_id") or "").strip()
    search = (request.args.get("q") or "").strip().lower()
    decorated_sessions = []
    for session_row in sessions:
        row = dict(session_row)
        if selected_client_id and row.get("client_id") != selected_client_id:
            continue
        row["session_kind_display"] = _session_kind_display(row.get("session_kind", ""))
        summary_search_text = ""
        if row.get("summary_storage_key"):
            summary_payload = storage.get_json(row["summary_storage_key"], row["client_id"]) or {}
            summary_search_text = " ".join(
                part for part in [
                    summary_payload.get("brief_overview", ""),
                    summary_payload.get("overview", ""),
                    summary_payload.get("full_summary", ""),
                ] if part
            ).strip()
        row["summary_search_text"] = summary_search_text
        if search and search not in summary_search_text.lower():
            continue
        decorated_sessions.append(row)
    db.close()
    return render_template(
        "consultant/sessions.html",
        brand=_brand_name(),
        theme="consultant",
        sessions=decorated_sessions,
        clients=clients,
        selected_client_id=selected_client_id,
        search=search,
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
    transcript = None
    transcript_text = ""
    biomarkers = None
    baseline = None
    biomarker_sections = []
    biomarker_headlines = []
    if session_row["summary_storage_key"]:
        summary = storage.get_json(session_row["summary_storage_key"], session_row["client_id"])
    if session_row["transcript_storage_key"]:
        transcript = storage.get_json(session_row["transcript_storage_key"], session_row["client_id"])
        if isinstance(transcript, dict):
            text = transcript.get("text")
            if isinstance(text, str):
                transcript_text = text.strip()
            elif isinstance(transcript.get("lines"), list):
                transcript_text = "\n".join(
                    str(line.get("text", "")).strip()
                    for line in transcript["lines"]
                    if isinstance(line, dict) and str(line.get("text", "")).strip()
                ).strip()
        elif isinstance(transcript, str):
            transcript_text = transcript.strip()
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
    messages = _message_preview_rows(db, session_row["client_id"], consultant_id, limit=50)
    db.close()
    return render_template(
        "consultant/session_detail.html",
        brand=_brand_name(),
        theme="consultant",
        session_row=session_row,
        session_kind_display=_session_kind_display(session_row["session_kind"]),
        summary=summary,
        transcript=transcript,
        transcript_text=transcript_text,
        transcript_enabled=bool(session_row["transcription_enabled"]),
        audio_biomarkers_enabled=bool(session_row["audio_biomarkers_enabled"]),
        video_biomarkers_enabled=bool(session_row["video_biomarkers_enabled"]),
        biomarkers=biomarkers,
        baseline=baseline,
        biomarker_headlines=biomarker_headlines,
        biomarker_sections=biomarker_sections,
        alerts=alerts,
        client_notes=session_row["notes_current"],
        client_direction=session_row["direction_current"],
        messages=messages,
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
    return redirect(tenant_url_for("web.consultant_client_detail", client_id=client_id))


@web_bp.get("/admin/dashboard")
@require_admin
def admin_dashboard():
    db = get_db(current_app.config)
    stats = {
        "vendors": db.execute("SELECT COUNT(*) AS c FROM vendors WHERE is_active = 1").fetchone()["c"],
        "consultants": db.execute("SELECT COUNT(*) AS c FROM consultants WHERE is_active = 1").fetchone()["c"],
        "clients": db.execute("SELECT COUNT(*) AS c FROM clients WHERE is_active = 1").fetchone()["c"],
        "sessions": db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
    }
    vendors = list_vendors(db)
    consultants = list_consultants(db)[:5]
    db.close()
    return render_template(
        "admin/dashboard.html",
        brand=_brand_name(),
        stats=stats,
        admin_email=session.get("admin_email"),
        vendors=vendors,
        consultants=consultants,
    )


@web_bp.route("/admin/vendors", methods=["GET", "POST"])
@require_admin
def admin_vendors():
    db = get_db(current_app.config)
    form_defaults = {}
    if request.method == "POST":
        action = request.form.get("action", "create").strip()
        if action == "create":
            slug = request.form.get("slug", "").strip().lower()
            name = request.form.get("name", "").strip()
            storage_root = request.form.get("storage_root", "").strip()
            www_root = request.form.get("www_root", "").strip()
            primary_host = request.form.get("primary_host", "").strip()
            if not slug or not name or not storage_root or not www_root:
                flash("Slug, name, storage root, and www root are required", "error")
            else:
                try:
                    create_vendor(
                        db,
                        slug=slug,
                        name=name,
                        storage_root=storage_root,
                        www_root=www_root,
                        primary_host=primary_host,
                    )
                    db.commit()
                    flash("Vendor created", "muted")
                    return redirect(tenant_url_for("web.admin_vendors"))
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("A vendor with that slug already exists", "error")
            form_defaults = {
                "slug": slug,
                "name": name,
                "storage_root": storage_root,
                "www_root": www_root,
                "primary_host": primary_host,
            }
        elif action == "update":
            vendor_id = request.form.get("vendor_id", "").strip()
            vendor = get_vendor_by_id(db, vendor_id)
            if not vendor:
                flash("Vendor not found", "error")
            else:
                update_vendor(
                    db,
                    vendor_id=vendor_id,
                    name=request.form.get("name", "").strip() or vendor["name"],
                    storage_root=request.form.get("storage_root", "").strip() or vendor["storage_root"],
                    www_root=request.form.get("www_root", "").strip() or vendor["www_root"],
                    primary_host=request.form.get("primary_host", "").strip(),
                )
                db.commit()
                flash("Vendor updated", "muted")
                return redirect(tenant_url_for("web.admin_vendors"))
    vendors = list_vendors(db)
    db.close()
    return render_template(
        "admin/vendors.html",
        brand=_brand_name(),
        vendors=vendors,
        form_defaults=form_defaults,
    )


@web_bp.route("/admin/consultants", methods=["GET", "POST"])
@require_admin
def admin_consultants():
    db = get_db(current_app.config)
    vendors = list_vendors(db)
    default_vendor = vendors[0]["id"] if vendors else ""
    form_defaults = {
        "vendor_id": default_vendor,
        "phone_country_code": "US",
        "escalation_phone_country_code": "US",
    }
    if request.method == "POST":
        vendor_id = request.form.get("vendor_id", "").strip() or default_vendor
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
                    "vendor_id": vendor_id,
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
                    brand=_brand_name(),
                    consultants=consultants,
                    phone_countries=country_options(),
                    form_defaults=form_defaults,
                )

            try:
                create_consultant(
                    db,
                    vendor_id=vendor_id,
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
                return redirect(tenant_url_for("web.admin_consultants"))
            except sqlite3.IntegrityError:
                db.rollback()
                flash("A consultant with that email already exists", "error")
    consultants = list_consultants(db)
    db.close()
    return render_template(
        "admin/consultants.html",
        brand=_brand_name(),
        consultants=consultants,
        vendors=vendors,
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
        brand=_brand_name(),
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
    return redirect(tenant_url_for("web.admin_consultants"))


@web_bp.get("/index.html")
def vendor_public_index():
    if not request.environ.get("mindfix.vendor_slug"):
        return redirect(tenant_url_for("web.home"))
    return _serve_vendor_public_asset("index.html")


@web_bp.get("/privacy.html")
def vendor_public_privacy():
    if not request.environ.get("mindfix.vendor_slug"):
        abort(404)
    return _serve_vendor_public_asset("privacy.html")


@web_bp.get("/terms.html")
def vendor_public_terms():
    if not request.environ.get("mindfix.vendor_slug"):
        abort(404)
    return _serve_vendor_public_asset("terms.html")


@web_bp.get("/css/<path:asset_path>")
def vendor_public_css(asset_path: str):
    if not request.environ.get("mindfix.vendor_slug"):
        abort(404)
    return _serve_vendor_public_asset(f"css/{asset_path}")


@web_bp.get("/img/<path:asset_path>")
def vendor_public_img(asset_path: str):
    if not request.environ.get("mindfix.vendor_slug"):
        abort(404)
    return _serve_vendor_public_asset(f"img/{asset_path}")
