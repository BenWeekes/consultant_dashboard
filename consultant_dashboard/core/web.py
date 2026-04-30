import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
import urllib.parse
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
    compose_client_display_name,
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
    is_gmail_address,
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
CLIENT_AUTH_COOKIE_NAME = "mindfix_client_auth"


def _brand_name() -> str:
    return current_branding().get("name") or current_app.config["BRAND_NAME"]


def _normalize_vendor_domain(domain: str) -> str:
    value = (domain or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urllib.parse.urlparse(value)
    host = (parsed.netloc or parsed.path or "").strip().lower()
    host = host.split("/", 1)[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _slug_from_vendor_domain(domain: str) -> str:
    host = _normalize_vendor_domain(domain)
    if not host:
        return ""
    base = host.split(":")[0]
    if base in {"localhost", "127.0.0.1"}:
        return "local"
    labels = [label for label in base.split(".") if label]
    if len(labels) >= 2:
        base = labels[-2]
    elif labels:
        base = labels[0]
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def _display_name_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in (slug or "").split("-") if part)


def _vendor_suggestions(domain: str) -> dict:
    host = _normalize_vendor_domain(domain)
    slug = _slug_from_vendor_domain(host)
    return {
        "domain": host,
        "slug": slug,
        "name": _display_name_from_slug(slug),
        "storage_root": f"/home/ubuntu/mindfix-runtime/vendors/{slug}" if slug else "",
        "www_root": f"/home/ubuntu/mindfix/consultant_dashboard/www/{slug}" if slug else "",
        "primary_host": f"https://{host}" if host else "",
    }


def _decode_client_auth_token(token: str) -> Optional[dict]:
    secret = current_app.config.get("CLIENT_AUTH_JWT_SECRET", "")
    if not secret or not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, signature_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        encoded_expected = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
        if not hmac.compare_digest(encoded_expected, signature_b64):
            return None
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        exp = int(payload.get("exp") or 0)
        if exp and exp < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def _current_client_claims() -> Optional[dict]:
    token = (request.cookies.get(CLIENT_AUTH_COOKIE_NAME) or "").strip()
    claims = _decode_client_auth_token(token)
    if claims:
        return claims
    query_token = (request.args.get("auth_token") or "").strip()
    return _decode_client_auth_token(query_token)


def _client_auth_redirect(*, reauth: bool = False):
    params = {
        "profile": _client_profile_name(),
        "return": request.url,
    }
    if reauth:
        params["reauth"] = "1"
    return redirect(f"{tenant_path('/auth/login')}?{urllib.parse.urlencode(params)}")


def _require_client_link_session(expected_client_id: str):
    claims = _current_client_claims()
    if not claims:
        return None, _client_auth_redirect()
    current_vendor = current_vendor_slug()
    if current_vendor and claims.get("vendor_slug") and claims.get("vendor_slug") != current_vendor:
        return None, _client_auth_redirect(reauth=True)
    if expected_client_id and claims.get("client_id") != expected_client_id:
        return None, _client_auth_redirect(reauth=True)
    return claims, None


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
    return (
        f"{_client_app_base_url()}/?profile={_client_profile_name()}"
        f"&autoconnect=true&appv=20260428b{suffix}"
    )


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
        f"&appv=20260428b"
        f"&join_bootstrap={bootstrap}"
        f"&returnurl={urllib.parse.quote(tenant_url_for('web.consultant_dashboard'), safe='')}"
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
            cta = "Enter Meeting Room" if (immediate or reminder_kind == "1m") else "Review and respond"
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


def _find_next_weekly_occurrence(db, meeting):
    return db.execute(
        """
        SELECT id
        FROM scheduled_meetings
        WHERE consultant_id = ?
          AND client_id = ?
          AND meeting_type = ?
          AND repeat_weekly = 1
          AND scheduled_start_at > ?
        ORDER BY scheduled_start_at ASC
        LIMIT 1
        """,
        (
            meeting["consultant_id"],
            meeting["client_id"],
            (meeting["meeting_type"] or "human").strip().lower(),
            meeting["scheduled_start_at"] or "",
        ),
    ).fetchone()


def _next_weekly_occurrence_times(meeting):
    start_at = _parse_iso_datetime(meeting["scheduled_start_at"])
    end_at = _parse_iso_datetime(meeting["scheduled_end_at"])
    if not start_at or not end_at:
        return None
    timezone_name = (meeting["timezone_name"] or "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
        timezone_name = "UTC"
    local_start = start_at.astimezone(tz)
    local_end = end_at.astimezone(tz)
    next_local_start = local_start + timedelta(days=7)
    next_local_end = local_end + timedelta(days=7)
    next_start_utc = next_local_start.astimezone(timezone.utc)
    next_end_utc = next_local_end.astimezone(timezone.utc)
    join_start, join_end = build_join_window(next_start_utc, next_end_utc)
    return {
        "timezone_name": timezone_name,
        "scheduled_start_at": iso_utc(next_start_utc),
        "scheduled_end_at": iso_utc(next_end_utc),
        "join_window_start_at": iso_utc(join_start),
        "join_window_end_at": iso_utc(join_end),
    }


def _ensure_next_weekly_occurrence(
    db,
    *,
    meeting_id: str,
    actor_type: str = "system",
    actor_id: str = "weekly-recurrence",
):
    meeting = get_scheduled_meeting(db, meeting_id)
    if not meeting or not bool(_meeting_field(meeting, "repeat_weekly", 0)):
        return None
    if meeting["status"] not in {"completed", "cancelled", "declined"}:
        return None
    if _find_next_weekly_occurrence(db, meeting):
        return None
    next_times = _next_weekly_occurrence_times(meeting)
    if not next_times:
        current_app.logger.warning(
            "weekly_recurrence_skipped_invalid_times meeting_id=%s",
            meeting_id,
        )
        return None

    access_token = new_access_token()
    access_link_id = create_client_access_link(
        db,
        client_id=meeting["client_id"],
        created_by=actor_id,
        token_hash=hash_access_token(access_token),
        expires_at=default_expiry(hours=24 * 30),
    )
    next_meeting_id = create_scheduled_meeting(
        db,
        client_id=meeting["client_id"],
        consultant_id=meeting["consultant_id"],
        meeting_type=meeting["meeting_type"] or "human",
        repeat_weekly=True,
        transcription_enabled=bool(meeting["transcription_enabled"]),
        audio_biomarkers_enabled=bool(meeting["audio_biomarkers_enabled"]),
        video_biomarkers_enabled=bool(meeting["video_biomarkers_enabled"]),
        transcription_provider=meeting["transcription_provider"] or "",
        transcription_language=meeting["transcription_language"] or "",
        title=meeting["title"] or "Weekly meeting",
        invite_message=meeting["invite_message"] or "",
        timezone_name=next_times["timezone_name"],
        scheduled_start_at=next_times["scheduled_start_at"],
        scheduled_end_at=next_times["scheduled_end_at"],
        join_window_start_at=next_times["join_window_start_at"],
        join_window_end_at=next_times["join_window_end_at"],
        channel_name=meeting["channel_name"],
        response_access_link_id=access_link_id,
    )
    next_meeting = get_scheduled_meeting_detail(db, next_meeting_id, consultant_id=meeting["consultant_id"])
    _send_meeting_invite(db, next_meeting, access_token)
    record_meeting_event(
        db,
        meeting_id=next_meeting_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="recurrence_scheduled",
        details={"source_meeting_id": meeting_id},
    )
    current_app.logger.info(
        "weekly_recurrence_created source_meeting_id=%s next_meeting_id=%s start=%s",
        meeting_id,
        next_meeting_id,
        next_times["scheduled_start_at"],
    )
    return next_meeting_id


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
        if (
            not meeting["reminder_24h_sent_at"]
            and created_at < (start_at - timedelta(hours=24))
            and now >= (start_at - timedelta(hours=24))
            and now < start_at
        ):
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


VOICE_TILE_KEYS = [
    ("stress", "Stress"),
    ("distress", "Distress"),
    ("burnout", "Burnout"),
    ("fatigue", "Fatigue"),
    ("depression_probability", "Depression"),
    ("anxiety_probability", "Anxiety"),
]

VIDEO_TILE_KEYS = [
    ("hrv_sdnn_ms", "HRV"),
    ("stress_index", "Cardiac Stress"),
    ("breathing_rate_bpm", "Breathing Rate"),
    ("blood_pressure", "Blood Pressure"),
    ("cardiac_workload", "Cardiac Workload"),
]

EMOTION_KEYS = ["angry", "disgusted", "fearful", "happy", "neutral", "other", "sad", "surprised"]


def _is_percent_metric(key: str) -> bool:
    return key not in {
        "heart_rate_bpm",
        "heart_rate",
        "hrv_sdnn_ms",
        "hrv",
        "breathing_rate_bpm",
        "breathing_rate",
        "stress_index",
        "systolic_bp",
        "diastolic_bp",
        "cardiac_workload",
        "blood_pressure",
    }


def _format_metric_number(key: str, value):
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if _is_percent_metric(key):
        return f"{round(numeric * 100)}%"
    if key == "stress_index":
        return f"{numeric:.2f}".rstrip("0").rstrip(".")
    return str(round(numeric))


def _metric_detail_suffix(key: str) -> str:
    return {
        "heart_rate_bpm": "bpm",
        "heart_rate": "bpm",
        "breathing_rate_bpm": "bpm",
        "breathing_rate": "bpm",
        "hrv_sdnn_ms": "ms",
        "hrv": "ms",
        "blood_pressure": "mmHg",
    }.get(key, "")


def _metric_value(source, key: str, field: str):
    if not isinstance(source, dict):
        return None
    metric = source.get(key)
    if isinstance(metric, dict):
        value = metric.get(field)
        if isinstance(value, (int, float)):
            return float(value)
    elif field == "avg" and isinstance(metric, (int, float)):
        return float(metric)
    return None


def _emotion_stat_label(source, field: str):
    if not isinstance(source, dict):
        return None
    ranked = []
    for key in EMOTION_KEYS:
        value = _metric_value(source, key, field)
        if isinstance(value, (int, float)) and value > 0:
            ranked.append((float(value), key))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    value, key = ranked[0]
    return {
        "label": key.replace("_", " ").title(),
        "value": f"{round(value * 100)}%",
    }


def _build_metric_row(source, key: str, label: str, *, window_sessions=None):
    if key == "blood_pressure":
        avg_sys = _metric_value(source, "systolic_bp", "avg")
        avg_dia = _metric_value(source, "diastolic_bp", "avg")
        max_sys = _metric_value(source, "systolic_bp", "max")
        max_dia = _metric_value(source, "diastolic_bp", "max")
        if avg_sys is None and avg_dia is None:
            return None
        average = f"{round(avg_sys) if avg_sys is not None else '—'}/{round(avg_dia) if avg_dia is not None else '—'}"
        maximum = (
            f"{round(max_sys) if max_sys is not None else '—'}/{round(max_dia) if max_dia is not None else '—'}"
            if max_sys is not None or max_dia is not None
            else None
        )
        return {
            "label": label,
            "average": average,
            "max": maximum,
            "window_sessions": window_sessions,
            "unit": "mmHg",
        }

    average_value = _metric_value(source, key, "avg")
    max_value = _metric_value(source, key, "max")
    if average_value is None and max_value is None:
        return None
    return {
        "label": label,
        "average": _format_metric_number(key, average_value) if average_value is not None else None,
        "max": _format_metric_number(key, max_value) if max_value is not None else None,
        "window_sessions": window_sessions,
        "unit": _metric_detail_suffix(key),
    }


def _empty_biomarker_message(*, audio_enabled: bool, video_enabled: bool, has_any_rows: bool) -> str | None:
    if has_any_rows:
        return None
    if not audio_enabled and not video_enabled:
        return "Audio and video biomarkers were not enabled for this session."
    if not audio_enabled and video_enabled:
        return "Audio biomarkers were not enabled for this session."
    if audio_enabled and not video_enabled:
        return "Video biomarkers were not enabled for this session."
    return "Biomarkers were enabled for this session, but no biomarker data was stored."


def _build_session_biomarker_view(biomarkers, *, audio_enabled: bool, video_enabled: bool):
    if not isinstance(biomarkers, dict):
        return {
            "headlines": [],
            "groups": [],
            "safety": None,
            "empty_message": _empty_biomarker_message(
                audio_enabled=audio_enabled,
                video_enabled=video_enabled,
                has_any_rows=False,
            ),
        }

    voice = biomarkers.get("voice") or {}
    vitals = biomarkers.get("vitals") or {}
    safety = biomarkers.get("safety") or {}

    stress_avg = _metric_value(voice, "stress", "avg")
    stress_max = _metric_value(voice, "stress", "max")
    heart_avg = _metric_value(vitals, "heart_rate_bpm", "avg") or _metric_value(vitals, "heart_rate", "avg")
    heart_max = _metric_value(vitals, "heart_rate_bpm", "max") or _metric_value(vitals, "heart_rate", "max")
    common_emotion = _emotion_stat_label(voice, "avg")
    peak_emotion = _emotion_stat_label(voice, "max")

    headlines = []
    if stress_avg is not None:
        headlines.append({
            "label": "Stress",
            "value": _format_metric_number("stress", stress_avg),
            "detail": f"Max { _format_metric_number('stress', stress_max) }" if stress_max is not None else None,
        })
    if heart_avg is not None:
        headlines.append({
            "label": "Heart Rate",
            "value": f"{_format_metric_number('heart_rate_bpm', heart_avg)} bpm",
            "detail": f"Max {_format_metric_number('heart_rate_bpm', heart_max)} bpm" if heart_max is not None else None,
        })
    if common_emotion:
        detail = f"Peak {peak_emotion['label']} ({peak_emotion['value']})" if peak_emotion else None
        headlines.append({
            "label": "Leading Emotion",
            "value": f"{common_emotion['label']} ({common_emotion['value']})",
            "detail": detail,
        })
    highest_level = safety.get("highest_level")
    highest_alert = safety.get("highest_alert")
    if highest_level is not None:
        headlines.append({
            "label": "Safety",
            "value": f"Level {highest_level}",
            "detail": highest_alert if highest_alert else None,
        })

    voice_rows = [row for key, label in VOICE_TILE_KEYS if (row := _build_metric_row(voice, key, label))]
    video_rows = [row for key, label in VIDEO_TILE_KEYS if (row := _build_metric_row(vitals, key, label))]

    safety_view = None
    if highest_level is not None or safety.get("highest_concerns") or safety.get("highest_recommended_actions"):
        safety_view = {
            "level": highest_level,
            "alert": highest_alert or None,
            "policy": safety.get("active_policy") or None,
            "concerns": list(safety.get("highest_concerns") or []),
            "actions": list(safety.get("highest_recommended_actions") or []),
        }

    groups = []
    if voice_rows:
        groups.append({"title": "Voice Biomarkers", "rows": voice_rows})
    if video_rows:
        groups.append({"title": "Video Biomarkers", "rows": video_rows})

    return {
        "headlines": headlines,
        "groups": groups,
        "safety": safety_view,
        "empty_message": _empty_biomarker_message(
            audio_enabled=audio_enabled,
            video_enabled=video_enabled,
            has_any_rows=bool(headlines or groups or safety_view),
        ),
    }


def _build_client_biomarker_view(baseline):
    if not isinstance(baseline, dict):
        return {
            "headlines": [],
            "groups": [],
            "safety": None,
            "empty_message": "No baseline established yet — needs at least one completed session.",
        }

    averages = baseline.get("averages") or {}
    maxes = baseline.get("maxes") or {}
    window_sessions = int(baseline.get("window_sessions") or 0)
    source = {}
    for key, value in averages.items():
        if isinstance(value, (int, float)):
            source[key] = {"avg": float(value)}
    for key, value in maxes.items():
        if isinstance(value, (int, float)):
            source.setdefault(key, {})["max"] = float(value)

    stress_avg = _metric_value(source, "stress", "avg")
    stress_max = _metric_value(source, "stress", "max")
    heart_avg = _metric_value(source, "heart_rate_bpm", "avg") or _metric_value(source, "heart_rate", "avg")
    heart_max = _metric_value(source, "heart_rate_bpm", "max") or _metric_value(source, "heart_rate", "max")
    common_emotion = _emotion_stat_label(source, "avg")
    peak_emotion = _emotion_stat_label(source, "max")

    headlines = []
    if stress_avg is not None:
        headlines.append({
            "label": "Stress",
            "value": _format_metric_number("stress", stress_avg),
            "detail": f"Max avg {_format_metric_number('stress', stress_max)} · {window_sessions} sessions" if stress_max is not None else f"{window_sessions} sessions",
        })
    if heart_avg is not None:
        detail = f"Max avg {_format_metric_number('heart_rate_bpm', heart_max)} bpm · {window_sessions} sessions" if heart_max is not None else f"{window_sessions} sessions"
        headlines.append({
            "label": "Heart Rate",
            "value": f"{_format_metric_number('heart_rate_bpm', heart_avg)} bpm",
            "detail": detail,
        })
    if common_emotion:
        detail = (
            f"Peak avg {peak_emotion['label']} ({peak_emotion['value']}) · {window_sessions} sessions"
            if peak_emotion
            else f"{window_sessions} sessions"
        )
        headlines.append({
            "label": "Leading Emotion",
            "value": f"{common_emotion['label']} ({common_emotion['value']})",
            "detail": detail,
        })

    latest_safety = baseline.get("latest_safety") or {}
    if latest_safety.get("highest_level") is not None:
        headlines.append({
            "label": "Safety",
            "value": f"Level {latest_safety['highest_level']}",
            "detail": latest_safety.get("highest_alert") or None,
        })

    voice_rows = [row for key, label in VOICE_TILE_KEYS if (row := _build_metric_row(source, key, label, window_sessions=window_sessions))]
    video_rows = [row for key, label in VIDEO_TILE_KEYS if (row := _build_metric_row(source, key, label, window_sessions=window_sessions))]

    safety_view = None
    if latest_safety.get("highest_level") is not None or latest_safety.get("highest_concerns") or latest_safety.get("highest_recommended_actions"):
        safety_view = {
            "level": latest_safety.get("highest_level"),
            "alert": latest_safety.get("highest_alert") or None,
            "policy": latest_safety.get("active_policy") or None,
            "concerns": list(latest_safety.get("highest_concerns") or []),
            "actions": list(latest_safety.get("highest_recommended_actions") or []),
        }

    groups = []
    if voice_rows:
        groups.append({"title": "Voice Biomarkers", "rows": voice_rows})
    if video_rows:
        groups.append({"title": "Video Biomarkers", "rows": video_rows})

    has_any_rows = bool(headlines or groups or safety_view)
    return {
        "headlines": headlines,
        "groups": groups,
        "safety": safety_view,
        "empty_message": None if has_any_rows else "No baseline established yet — needs at least one completed session.",
    }


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
    average_metrics = {}
    max_metrics = {}
    latest_safety = None
    successful_payloads = 0
    for row in biomarker_rows:
        payload = storage.get_json(row["biomarker_storage_key"], client_id)
        if not payload:
            continue
        successful_payloads += 1
        saw_group_metrics = False
        for group_name in ("voice", "vitals"):
            group = payload.get(group_name) or {}
            for key, metric in group.items():
                if not isinstance(metric, dict):
                    continue
                saw_group_metrics = True
                avg_value = metric.get("avg")
                max_value = metric.get("max")
                if isinstance(avg_value, (int, float)):
                    average_metrics.setdefault(key, []).append(float(avg_value))
                if isinstance(max_value, (int, float)):
                    max_metrics.setdefault(key, []).append(float(max_value))
        if not saw_group_metrics:
            # Backward compatibility for older biomarker payloads that only
            # stored structured metric objects under `averages`.
            for key, metric in (payload.get("averages") or {}).items():
                if not isinstance(metric, dict):
                    continue
                avg_value = metric.get("avg")
                max_value = metric.get("max")
                if isinstance(avg_value, (int, float)):
                    average_metrics.setdefault(key, []).append(float(avg_value))
                if isinstance(max_value, (int, float)):
                    max_metrics.setdefault(key, []).append(float(max_value))
        safety = payload.get("safety") or {}
        if (
            latest_safety is None
            and isinstance(safety, dict)
            and (
                safety.get("highest_level") is not None
                or safety.get("highest_alert")
                or safety.get("active_policy")
                or safety.get("highest_concerns")
                or safety.get("highest_recommended_actions")
            )
        ):
            latest_safety = {
                "highest_level": safety.get("highest_level"),
                "highest_alert": safety.get("highest_alert"),
                "highest_concerns": list(safety.get("highest_concerns") or []),
                "highest_recommended_actions": list(safety.get("highest_recommended_actions") or []),
                "active_policy": safety.get("active_policy") or "",
            }

    if successful_payloads:
        baseline = {
            "window_sessions": successful_payloads,
            "averages": {
                key: round(sum(values) / len(values), 4)
                for key, values in average_metrics.items()
                if values
            },
            "maxes": {
                key: round(sum(values) / len(values), 4)
                for key, values in max_metrics.items()
                if values
            },
            "latest_safety": latest_safety or {},
        }
        baseline_key = f"clients/{client_id}/baseline.json.enc"
        storage.put_json(baseline_key, client_id, baseline)

    session_rows = db.execute(
        """
        SELECT id, session_kind, meeting_id, summary_storage_key,
               COALESCE(ended_at, started_at, created_at) AS session_at
        FROM sessions
        WHERE client_id = ?
        ORDER BY COALESCE(ended_at, started_at, created_at) DESC
        """,
        (client_id,),
    ).fetchall()

    def _normalize_summary_payload(summary):
        if not isinstance(summary, dict):
            return {}
        brief = (summary.get("brief_overview") or summary.get("overview") or "").strip()
        full = (summary.get("full_summary") or brief).strip()
        return {
            "brief_overview": brief,
            "overview": brief,
            "full_summary": full,
            "risk_overview": (summary.get("risk_overview") or "").strip(),
            "follow_up": (summary.get("follow_up") or "").strip(),
            "biomarker_summary": (summary.get("biomarker_summary") or "").strip(),
        }

    def _is_ai_session(row) -> bool:
        kind = (row["session_kind"] or "").strip().lower()
        return kind == "avatar_ai_session"

    def _build_personal_summary_payload(kind_label: str, rows):
        session_count = len(rows)
        if not session_count:
            return None
        def _stringify_dt(value):
            if not value:
                return ""
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            return str(value)
        summaries = []
        for row in rows:
            if not row["summary_storage_key"]:
                continue
            payload = storage.get_json(row["summary_storage_key"], client_id)
            normalized = _normalize_summary_payload(payload)
            if normalized.get("brief_overview") or normalized.get("full_summary"):
                normalized["session_at"] = row["session_at"] or ""
                summaries.append(normalized)

        if not summaries:
            return {
                "updated_at": iso_utc(utc_now()),
                "session_count": session_count,
                "window_sessions": 0,
                "brief_overview": f"No {kind_label.lower()} summary stored yet.",
                "full_summary": "",
                "key_facts": [],
                "open_threads": [],
                "latest_session_at": _stringify_dt(rows[0]["session_at"]),
            }

        recent = summaries[:6]
        brief_lines = []
        seen_briefs = set()
        for item in recent:
            brief = item["brief_overview"]
            if brief and brief not in seen_briefs:
                seen_briefs.add(brief)
                brief_lines.append(brief)
            if len(brief_lines) >= 3:
                break

        risk_lines = []
        follow_up_lines = []
        key_facts = []
        seen_risks = set()
        seen_follow = set()
        seen_facts = set()
        for item in recent:
            risk = item["risk_overview"]
            follow = item["follow_up"]
            if risk and risk not in seen_risks:
                seen_risks.add(risk)
                risk_lines.append(risk)
            if follow and follow not in seen_follow:
                seen_follow.add(follow)
                follow_up_lines.append(follow)
            for sentence in re.split(r"(?<=[.!?])\s+", item["full_summary"] or ""):
                cleaned = " ".join(sentence.split()).strip()
                if len(cleaned) < 40:
                    continue
                if cleaned in seen_facts:
                    continue
                seen_facts.add(cleaned)
                key_facts.append(cleaned)
                if len(key_facts) >= 5:
                    break
            if len(key_facts) >= 5:
                break

        latest_at = _stringify_dt(rows[0]["session_at"])
        brief_overview = " ".join(brief_lines[:2]).strip() or f"{session_count} {kind_label.lower()} sessions on record."
        full_parts = [
            f"{session_count} {kind_label.lower()} sessions are on record for this client.",
            "Recent themes: " + " ".join(brief_lines[:3]).strip() if brief_lines else "",
            "Key facts: " + " ".join(key_facts[:3]).strip() if key_facts else "",
            "Risk pattern: " + " ".join(risk_lines[:2]).strip() if risk_lines else "",
            "Open threads: " + " ".join(follow_up_lines[:2]).strip() if follow_up_lines else "",
        ]
        full_summary = " ".join(part for part in full_parts if part).strip()

        return {
            "updated_at": iso_utc(utc_now()),
            "session_count": session_count,
            "window_sessions": len(summaries),
            "brief_overview": brief_overview,
            "overview": brief_overview,
            "full_summary": full_summary,
            "key_facts": key_facts[:5],
            "open_threads": follow_up_lines[:5],
            "latest_session_at": latest_at,
        }

    ai_rows = [row for row in session_rows if _is_ai_session(row)]
    human_rows = [row for row in session_rows if not _is_ai_session(row)]
    ai_summary_payload = _build_personal_summary_payload("AI", ai_rows)
    human_summary_payload = _build_personal_summary_payload("Human", human_rows)
    ai_summary_key = None
    human_summary_key = None
    if ai_summary_payload:
        ai_summary_key = f"clients/{client_id}/ai_summary.json.enc"
        storage.put_json(ai_summary_key, client_id, ai_summary_payload)
    if human_summary_payload:
        human_summary_key = f"clients/{client_id}/human_summary.json.enc"
        storage.put_json(human_summary_key, client_id, human_summary_payload)

    db.execute(
        """
        UPDATE clients
        SET latest_summary_storage_key = ?,
            baseline_storage_key = ?,
            ai_summary_storage_key = ?,
            human_summary_storage_key = ?,
            ai_session_count = ?,
            human_session_count = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            latest_summary_key,
            baseline_key,
            ai_summary_key,
            human_summary_key,
            len(ai_rows),
            len(human_rows),
            client_id,
        ),
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


def _client_name_fields_from_form(req) -> tuple[str, str, str]:
    first_name = req.form.get("first_name", "").strip()
    last_name = req.form.get("last_name", "").strip()
    return first_name, last_name, compose_client_display_name(first_name, last_name)


def _client_demographics_from_form(req) -> tuple[Optional[int], str]:
    raw_year = (req.form.get("year_of_birth") or "").strip()
    sex = (req.form.get("sex") or "").strip().lower()
    year_of_birth = None
    if raw_year:
        try:
            year_of_birth = int(raw_year)
        except ValueError as exc:
            raise ValueError("Year of birth must be a valid year.") from exc
        current_year = datetime.now(timezone.utc).year
        if year_of_birth < 1900 or year_of_birth > current_year:
            raise ValueError("Year of birth must be between 1900 and the current year.")
    if sex and sex not in {"male", "female"}:
        raise ValueError("Sex must be male or female.")
    return year_of_birth, sex


@web_bp.route("/consultant/clients/new", methods=["GET", "POST"])
@require_consultant
def consultant_client_new():
    consultant_id = session.get("consultant_id")
    db = get_db(current_app.config)
    form_defaults = {
        "phone_country_code": "US",
        "escalation_phone_country_code": "US",
        "sex": "",
    }
    if request.method == "POST":
        first_name, last_name, display_name = _client_name_fields_from_form(request)
        if not first_name:
            flash("First name is required", "error")
        else:
            email = request.form.get("email", "").strip()
            gmail_email = is_gmail_address(email)
            phone_country_code = request.form.get("phone_country_code", "US").strip().upper()
            escalation_phone_country_code = request.form.get("escalation_phone_country_code", phone_country_code).strip().upper()
            raw_phone_number = request.form.get("phone_number", "").strip()
            raw_escalation_phone = request.form.get("escalation_phone_number", "").strip()
            initial_password = "" if gmail_email else request.form.get("password", "").strip()
            form_defaults = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "password": initial_password,
                "phone_number": raw_phone_number,
                "phone_country_code": phone_country_code,
                "notification_email": request.form.get("notification_email", "").strip(),
                "escalation_phone_number": raw_escalation_phone,
                "escalation_phone_country_code": escalation_phone_country_code,
                "year_of_birth": (request.form.get("year_of_birth") or "").strip(),
                "sex": (request.form.get("sex") or "").strip().lower(),
                "notes": request.form.get("notes", "").strip(),
                "direction": request.form.get("direction", "").strip(),
            }
            if initial_password and (not email or not raw_phone_number):
                flash("Email and phone number are required when setting a client password.", "error")
                db.close()
                return _render_consultant_client_new_form(form_defaults=form_defaults)
            if initial_password and len(initial_password) < 8:
                flash("Password must be at least 8 characters.", "error")
                db.close()
                return _render_consultant_client_new_form(form_defaults=form_defaults)
            try:
                year_of_birth, sex = _client_demographics_from_form(request)
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
                first_name=first_name,
                last_name=last_name,
                email=email,
                password_hash=generate_password_hash(initial_password, method="pbkdf2:sha256") if initial_password else "",
                phone_number=phone_number,
                notification_email=notification_email,
                escalation_phone_number=escalation_phone_number,
                year_of_birth=year_of_birth,
                sex=sex,
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
            first_name, last_name, display_name = _client_name_fields_from_form(request)
            email = request.form.get("email", "").strip()
            gmail_email = is_gmail_address(email)
            phone_country_code = request.form.get("phone_country_code", "US").strip().upper()
            escalation_phone_country_code = request.form.get("escalation_phone_country_code", phone_country_code).strip().upper()
            raw_phone_number = request.form.get("phone_number", "").strip()
            raw_escalation_phone = request.form.get("escalation_phone_number", "").strip()
            notification_email = request.form.get("notification_email", "").strip() or email
            notes = request.form.get("notes", "").strip()
            direction = request.form.get("direction", "").strip()
            reset_password = "" if gmail_email else request.form.get("password", "").strip()

            if not first_name:
                flash("First name is required", "error")
            else:
                try:
                    year_of_birth, sex = _client_demographics_from_form(request)
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
                        first_name=first_name,
                        last_name=last_name,
                        email=email,
                        phone_number=phone_number,
                        notification_email=notification_email,
                        escalation_phone_number=escalation_phone_number,
                        year_of_birth=year_of_birth,
                        sex=sex,
                        notes=notes,
                        direction=direction,
                    )
                    if gmail_email:
                        update_client_password(
                            db,
                            client_id=client_id,
                            password_hash="",
                        )
                    if reset_password:
                        if len(reset_password) < 8:
                            raise ValueError("Password must be at least 8 characters.")
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
                        flash("Client password updated", "muted")
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
    if not client["ai_summary_storage_key"] or not client["human_summary_storage_key"]:
        _refresh_client_derived_state(db, storage, client_id)
        db.commit()
        client = get_client_detail(db, client_id, consultant_id=consultant_id)
    baseline = None
    ai_personal_summary = None
    human_personal_summary = None
    if client["baseline_storage_key"]:
        baseline = storage.get_json(client["baseline_storage_key"], client_id)
    if client["ai_summary_storage_key"]:
        ai_personal_summary = storage.get_json(client["ai_summary_storage_key"], client_id)
    if client["human_summary_storage_key"]:
        human_personal_summary = storage.get_json(client["human_summary_storage_key"], client_id)
    session_summaries = {}
    for session_row in sessions:
        summary_payload = None
        if session_row["summary_storage_key"]:
            summary_payload = storage.get_json(session_row["summary_storage_key"], client_id)
        session_summaries[session_row["id"]] = summary_payload or {}
    messages = _message_preview_rows(db, client_id, consultant_id, limit=20)
    client_biomarkers = _build_client_biomarker_view(baseline)
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
        baseline=baseline,
        biomarker_headlines=client_biomarkers["headlines"],
        biomarker_groups=client_biomarkers["groups"],
        biomarker_safety=client_biomarkers["safety"],
        biomarker_empty_message=client_biomarkers["empty_message"],
        ai_personal_summary=ai_personal_summary,
        human_personal_summary=human_personal_summary,
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
                _ensure_next_weekly_occurrence(
                    db,
                    meeting_id=meeting_id,
                    actor_type="consultant",
                    actor_id=consultant_id,
                )
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
                _ensure_next_weekly_occurrence(
                    db,
                    meeting_id=meeting_id,
                    actor_type="consultant",
                    actor_id=consultant_id,
                )
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
    meeting_biomarkers = _build_session_biomarker_view(
        linked_biomarkers,
        audio_enabled=bool(meeting["audio_biomarkers_enabled"]),
        video_enabled=bool(meeting["video_biomarkers_enabled"]),
    )
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
        biomarker_headlines=meeting_biomarkers["headlines"],
        biomarker_groups=meeting_biomarkers["groups"],
        biomarker_safety=meeting_biomarkers["safety"],
        biomarker_empty_message=meeting_biomarkers["empty_message"],
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
    _claims, auth_redirect = _require_client_link_session(meeting["client_id"])
    if auth_redirect is not None:
        db.close()
        return auth_redirect
    target_meeting = _find_guest_join_target(db, meeting, now=utc_now())
    db.close()
    if (target_meeting["meeting_type"] or "human").strip().lower() == "ai":
        join_url = _build_ai_join_url(target_meeting["id"])
    else:
        join_url = (
            f"{_client_app_base_url()}/?meeting_mode=true"
            f"&profile={_client_profile_name()}"
            f"&appv=20260428b"
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
    if meeting:
        _claims, auth_redirect = _require_client_link_session(meeting["client_id"])
        if auth_redirect is not None:
            db.close()
            return auth_redirect
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

    _claims, auth_redirect = _require_client_link_session(link["client_id"])
    if auth_redirect is not None:
        db.close()
        return auth_redirect

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
    _claims, auth_redirect = _require_client_link_session(link["client_id"])
    if auth_redirect is not None:
        db.close()
        return auth_redirect
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

    _claims, auth_redirect = _require_client_link_session(meeting["client_id"])
    if auth_redirect is not None:
        db.close()
        return auth_redirect

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
                _ensure_next_weekly_occurrence(
                    db,
                    meeting_id=meeting["id"],
                    actor_type="guest",
                    actor_id=meeting["client_id"],
                )
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


@web_bp.route("/consultant/sessions/<session_id>", methods=["GET"])
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
    transcript = None
    transcript_text = ""
    biomarkers = None
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
    session_biomarkers = _build_session_biomarker_view(
        biomarkers,
        audio_enabled=bool(session_row["audio_biomarkers_enabled"]),
        video_enabled=bool(session_row["video_biomarkers_enabled"]),
    )
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
        biomarker_headlines=session_biomarkers["headlines"],
        biomarker_groups=session_biomarkers["groups"],
        biomarker_safety=session_biomarkers["safety"],
        biomarker_empty_message=session_biomarkers["empty_message"],
        alerts=alerts,
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
    decorated_vendors = []
    for vendor in vendors:
        row = dict(vendor)
        row["consultant_count"] = db.execute(
            "SELECT COUNT(*) AS c FROM consultants WHERE vendor_id = ? AND is_active = 1",
            (vendor["id"],),
        ).fetchone()["c"]
        row["client_count"] = db.execute(
            "SELECT COUNT(*) AS c FROM clients WHERE vendor_id = ? AND is_active = 1",
            (vendor["id"],),
        ).fetchone()["c"]
        decorated_vendors.append(row)
    consultants = list_consultants(db)[:5]
    db.close()
    return render_template(
        "admin/dashboard.html",
        brand=_brand_name(),
        stats=stats,
        admin_email=session.get("admin_email"),
        vendors=decorated_vendors,
        consultants=consultants,
    )


@web_bp.get("/admin/vendors")
@require_admin
def admin_vendors():
    db = get_db(current_app.config)
    vendors = []
    for vendor in list_vendors(db):
        row = dict(vendor)
        row["consultant_count"] = db.execute(
            "SELECT COUNT(*) AS c FROM consultants WHERE vendor_id = ? AND is_active = 1",
            (vendor["id"],),
        ).fetchone()["c"]
        row["client_count"] = db.execute(
            "SELECT COUNT(*) AS c FROM clients WHERE vendor_id = ? AND is_active = 1",
            (vendor["id"],),
        ).fetchone()["c"]
        vendors.append(row)
    search = (request.args.get("q") or "").strip().lower()
    if search:
        filtered = []
        for vendor in vendors:
            haystack = " ".join(
                str(vendor.get(key) or "")
                for key in ("name", "slug", "primary_host", "storage_root", "www_root")
            ).lower()
            if search in haystack:
                filtered.append(vendor)
        vendors = filtered
    db.close()
    return render_template(
        "admin/vendors.html",
        brand=_brand_name(),
        vendors=vendors,
        search=search,
    )


@web_bp.route("/admin/vendors/new", methods=["GET", "POST"])
@require_admin
def admin_vendor_new():
    form_defaults = {}
    if request.method == "POST":
        domain = request.form.get("domain", "").strip()
        suggestions = _vendor_suggestions(domain)
        slug = (request.form.get("slug", "").strip().lower() or suggestions["slug"])
        name = request.form.get("name", "").strip() or suggestions["name"]
        storage_root = request.form.get("storage_root", "").strip() or suggestions["storage_root"]
        www_root = request.form.get("www_root", "").strip() or suggestions["www_root"]
        primary_host = suggestions["primary_host"]
        form_defaults = {
            "domain": suggestions["domain"] or domain,
            "slug": slug,
            "name": request.form.get("name", "").strip() or suggestions["name"],
            "storage_root": request.form.get("storage_root", "").strip() or suggestions["storage_root"],
            "www_root": request.form.get("www_root", "").strip() or suggestions["www_root"],
        }
        if not suggestions["domain"] or not slug or not name or not storage_root or not www_root:
            flash("Domain, slug, name, storage root, and www root are required", "error")
        else:
            db = get_db(current_app.config)
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
                flash("A vendor for that domain already exists", "error")
            finally:
                db.close()
    return render_template(
        "admin/vendor_new.html",
        brand=_brand_name(),
        form_defaults=form_defaults,
    )


@web_bp.route("/admin/vendors/<vendor_id>", methods=["GET", "POST"])
@require_admin
def admin_vendor_detail(vendor_id: str):
    db = get_db(current_app.config)
    vendor = get_vendor_by_id(db, vendor_id)
    if not vendor:
        db.close()
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        storage_root = request.form.get("storage_root", "").strip()
        www_root = request.form.get("www_root", "").strip()
        domain = request.form.get("domain", "").strip()
        slug = (request.form.get("slug", "").strip().lower() or vendor["slug"])
        suggestions = _vendor_suggestions(domain)
        primary_host = suggestions["primary_host"]
        if not name or not storage_root or not www_root or not suggestions["domain"] or not slug:
            flash("Domain, slug, name, storage root, and www root are required", "error")
        else:
            try:
                db.execute(
                    "UPDATE vendors SET slug = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (slug, vendor_id),
                )
                update_vendor(
                    db,
                    vendor_id=vendor_id,
                    name=name,
                    storage_root=storage_root,
                    www_root=www_root,
                    primary_host=primary_host,
                )
                db.commit()
                flash("Vendor updated", "muted")
                vendor = get_vendor_by_id(db, vendor_id)
            except sqlite3.IntegrityError:
                db.rollback()
                flash("A vendor with that slug already exists", "error")
    db.close()
    return render_template(
        "admin/vendor_detail.html",
        brand=_brand_name(),
        vendor=vendor,
        domain=_normalize_vendor_domain(vendor["primary_host"]),
    )


@web_bp.get("/admin/consultants")
@require_admin
def admin_consultants():
    db = get_db(current_app.config)
    vendors = list_vendors(db)
    consultants = list_consultants(db)
    search = (request.args.get("q") or "").strip().lower()
    if search:
        vendors_by_id = {vendor["id"]: vendor for vendor in vendors}
        filtered = []
        for consultant in consultants:
            vendor_name = (vendors_by_id.get(consultant["vendor_id"], {}) or {}).get("name", "")
            haystack = f"{consultant['name']} {consultant['email']} {consultant['phone_number']} {vendor_name}".lower()
            if search in haystack:
                filtered.append(consultant)
        consultants = filtered
    db.close()
    return render_template(
        "admin/consultants.html",
        brand=_brand_name(),
        consultants=consultants,
        vendors=vendors,
        vendors_by_id={vendor["id"]: vendor for vendor in vendors},
        search=search,
    )


@web_bp.route("/admin/consultants/new", methods=["GET", "POST"])
@require_admin
def admin_consultant_new():
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
                db.close()
                return redirect(tenant_url_for("web.admin_consultants"))
            except ValueError as exc:
                db.rollback()
                flash(str(exc), "error")
            except sqlite3.IntegrityError:
                db.rollback()
                flash("A consultant with that email already exists", "error")
    db.close()
    return render_template(
        "admin/consultant_new.html",
        brand=_brand_name(),
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
