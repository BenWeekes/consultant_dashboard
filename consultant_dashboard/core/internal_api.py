import hashlib
import hmac
import time
from typing import Dict, List

from flask import Blueprint, current_app, jsonify, request

from .db import (
    create_session_alert,
    get_client_context,
    get_db,
    log_audit,
    resolve_client_identity,
    upsert_session,
)
from .storage import EncryptedStorage

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
    row = resolve_client_identity(
        db,
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
        "is_active": bool(row["is_active"]),
    }


@internal_bp.get("/client-context")
def client_context():
    client_id = request.args.get("client_id", "").strip()
    if not client_id:
        return jsonify({"error": "client_id required"}), 400
    db = get_db(current_app.config)
    result = get_client_context(db, client_id)
    db.close()
    if not result:
        return jsonify({"error": "client not found"}), 404
    client, alerts = result
    storage = EncryptedStorage(current_app.config["STORAGE_ROOT"], current_app.config["MASTER_KEY"])
    latest_summary = None
    baseline = None
    if client["latest_summary_storage_key"]:
        latest_summary = storage.get_json(client["latest_summary_storage_key"], client_id)
    if client["baseline_storage_key"]:
        baseline = storage.get_json(client["baseline_storage_key"], client_id)
    return {
        "client_id": client["id"],
        "display_name": client["display_name"],
        "consultant_id": client["consultant_id"],
        "consultant_name": client["consultant_name"],
        "notes": client["notes_current"],
        "direction": client["direction_current"],
        "latest_summary": latest_summary,
        "baseline": baseline,
        "alerts": [dict(a) for a in alerts],
    }


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
    averages = {
        key: round(sum(values) / len(values), 4)
        for key, values in metrics.items()
        if values
    }
    return {
        "window_sessions": len(rows),
        "averages": averages,
    }


@internal_bp.post("/session-complete")
def session_complete():
    payload = request.get_json(force=True)
    client_id = payload["client_id"]
    session_id = payload["session_id"]
    storage = EncryptedStorage(current_app.config["STORAGE_ROOT"], current_app.config["MASTER_KEY"])

    summary_key = None
    biomarker_key = None
    alert_keys = []
    if payload.get("summary"):
        summary_key = f"clients/{client_id}/sessions/{session_id}/summary.json.enc"
        storage.put_json(summary_key, client_id, payload["summary"])
    if payload.get("biomarkers"):
        biomarker_key = f"clients/{client_id}/sessions/{session_id}/biomarkers.json.enc"
        storage.put_json(biomarker_key, client_id, payload["biomarkers"])

    db = get_db(current_app.config)
    upsert_session(
        db,
        session_id=session_id,
        client_id=client_id,
        consultant_id=payload.get("consultant_id"),
        profile_name=payload.get("profile", "default"),
        channel_name=payload.get("channel", ""),
        started_at=payload.get("started_at", ""),
        ended_at=payload.get("ended_at", ""),
        duration_seconds=int(payload.get("duration_seconds", 0)),
        status=payload.get("status", "completed"),
        summary_storage_key=summary_key,
        biomarker_storage_key=biomarker_key,
        memory_storage_key=payload.get("memory_storage_key"),
        urgent_escalation=1 if payload.get("urgent_escalation") else 0,
        escalation_reason=payload.get("escalation_reason", ""),
    )
    baseline_key = None
    if biomarker_key:
        baseline = _compute_baseline(storage, db, client_id)
        baseline_key = f"clients/{client_id}/baseline.json.enc"
        storage.put_json(baseline_key, client_id, baseline)
        db.execute(
            "UPDATE clients SET baseline_storage_key = ?, latest_summary_storage_key = COALESCE(?, latest_summary_storage_key), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (baseline_key, summary_key, client_id),
        )
    elif summary_key:
        db.execute(
            "UPDATE clients SET latest_summary_storage_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (summary_key, client_id),
        )
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
    return {"ok": True, "session_id": session_id, "baseline_storage_key": baseline_key}
