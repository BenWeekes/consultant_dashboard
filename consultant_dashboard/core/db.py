import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .client_identity import build_identity_hashes

def get_db(config: dict) -> sqlite3.Connection:
    db = sqlite3.connect(config["DB_PATH"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db(config: dict) -> None:
    db = get_db(config)
    schema_path = Path(__file__).with_name("schema.sql")
    db.executescript(schema_path.read_text(encoding="utf-8"))
    _ensure_migrations(db)
    db.commit()
    db.close()


def _ensure_migrations(db: sqlite3.Connection) -> None:
    client_columns = {row["name"] for row in db.execute("PRAGMA table_info(clients)").fetchall()}
    if "password_hash" not in client_columns:
        db.execute("ALTER TABLE clients ADD COLUMN password_hash TEXT")


def create_consultant(
    db: sqlite3.Connection,
    *,
    email: str,
    name: str,
    phone_number: str,
    password_hash: str,
    notification_email: str,
    escalation_phone_number: str,
) -> None:
    db.execute(
        """
        INSERT INTO consultants (
            email, password_hash, name, phone_number,
            notification_email, escalation_phone_number, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (
            email.lower().strip(),
            password_hash,
            name.strip(),
            phone_number.strip(),
            notification_email.strip(),
            escalation_phone_number.strip(),
        ),
    )


def get_consultant_by_email(db: sqlite3.Connection, email: str):
    return db.execute(
        "SELECT * FROM consultants WHERE email = ? AND is_active = 1",
        (email.lower().strip(),),
    ).fetchone()


def get_consultant_by_id(db: sqlite3.Connection, consultant_id: str):
    return db.execute(
        "SELECT * FROM consultants WHERE id = ? AND is_active = 1",
        (consultant_id,),
    ).fetchone()


def update_consultant(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    email: str,
    name: str,
    phone_number: str,
    notification_email: str,
    escalation_phone_number: str,
) -> None:
    db.execute(
        """
        UPDATE consultants
        SET email = ?,
            name = ?,
            phone_number = ?,
            notification_email = ?,
            escalation_phone_number = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            email.lower().strip(),
            name.strip(),
            phone_number.strip(),
            notification_email.strip(),
            escalation_phone_number.strip(),
            consultant_id,
        ),
    )


def update_consultant_password(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    password_hash: str,
) -> None:
    db.execute(
        """
        UPDATE consultants
        SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (password_hash, consultant_id),
    )


def deactivate_consultant(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
) -> None:
    db.execute(
        """
        UPDATE consultants
        SET is_active = 0, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (consultant_id,),
    )


def update_client(
    db: sqlite3.Connection,
    *,
    client_id: str,
    display_name: str,
    email: str,
    phone_number: str,
    notification_email: str,
    escalation_phone_number: str,
    notes: str,
    direction: str,
) -> None:
    db.execute(
        """
        UPDATE clients
        SET display_name = ?,
            email = ?,
            phone_number = ?,
            notification_email = ?,
            escalation_phone_number = ?,
            notes_current = ?,
            direction_current = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            display_name.strip(),
            email.strip(),
            phone_number.strip(),
            notification_email.strip(),
            escalation_phone_number.strip(),
            notes.strip(),
            direction.strip(),
            client_id,
        ),
    )


def update_client_password(
    db: sqlite3.Connection,
    *,
    client_id: str,
    password_hash: str,
) -> None:
    db.execute(
        """
        UPDATE clients
        SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (password_hash, client_id),
    )


def list_consultants(db: sqlite3.Connection):
    return db.execute(
        """
        SELECT c.*,
               COUNT(DISTINCT cc.client_id) AS client_count,
               COUNT(DISTINCT s.id) AS session_count
        FROM consultants c
        LEFT JOIN consultant_clients cc ON cc.consultant_id = c.id
        LEFT JOIN sessions s ON s.consultant_id = c.id
        WHERE c.is_active = 1
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """
    ).fetchall()


def create_client(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    display_name: str,
    email: str,
    password_hash: str = "",
    phone_number: str,
    notification_email: str,
    escalation_phone_number: str,
    notes: str,
    direction: str,
) -> str:
    db.execute(
        """
        INSERT INTO clients (
            display_name, email, password_hash, phone_number, notification_email,
            escalation_phone_number, notes_current, direction_current,
            created_by_consultant_id, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            display_name.strip(),
            email.strip(),
            password_hash.strip(),
            phone_number.strip(),
            notification_email.strip(),
            escalation_phone_number.strip(),
            notes.strip(),
            direction.strip(),
            consultant_id,
        ),
    )
    client_id = db.execute("SELECT id FROM clients WHERE rowid = last_insert_rowid()").fetchone()["id"]
    db.execute(
        """
        INSERT OR IGNORE INTO consultant_clients (consultant_id, client_id, role)
        VALUES (?, ?, 'primary')
        """,
        (consultant_id, client_id),
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
    return client_id


def get_client_by_email(db: sqlite3.Connection, email: str):
    return db.execute(
        """
        SELECT c.*, cc.consultant_id
        FROM clients c
        LEFT JOIN consultant_clients cc ON cc.client_id = c.id
        WHERE c.email = ? AND c.is_active = 1
        ORDER BY cc.created_at DESC
        LIMIT 1
        """,
        (email.lower().strip(),),
    ).fetchone()


def upsert_client_auth_identity(
    db: sqlite3.Connection,
    *,
    client_id: str,
    google_sub_hash: str = "",
    email_hash: str = "",
    normalized_name_hash: str = "",
    phone_hash: str = "",
) -> None:
    existing = db.execute(
        "SELECT id FROM client_auth_identities WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    params = (
        google_sub_hash or None,
        email_hash or None,
        normalized_name_hash or None,
        phone_hash or None,
        client_id,
    )
    if existing:
        db.execute(
            """
            UPDATE client_auth_identities
            SET google_sub_hash = ?,
                email_hash = ?,
                normalized_name_hash = ?,
                phone_hash = ?,
                last_verified_at = CURRENT_TIMESTAMP
            WHERE client_id = ?
            """,
            params,
        )
        return
    db.execute(
        """
        INSERT INTO client_auth_identities (
            client_id, google_sub_hash, email_hash, normalized_name_hash, phone_hash, last_verified_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            client_id,
            google_sub_hash or None,
            email_hash or None,
            normalized_name_hash or None,
            phone_hash or None,
        ),
    )


def log_audit(
    db: sqlite3.Connection,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str = "",
    target_id: str = "",
    session_id: str = "",
    request_id: str = "",
    ip_address: str = "",
    user_agent: str = "",
    details: Optional[Dict] = None,
) -> None:
    db.execute(
        """
        INSERT INTO audit_log (
            actor_type, actor_id, action, target_type, target_id, session_id,
            request_id, ip_address, user_agent, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_type,
            actor_id,
            action,
            target_type,
            target_id,
            session_id,
            request_id,
            ip_address,
            user_agent,
            json.dumps(details or {}),
        ),
    )


def upsert_session(
    db: sqlite3.Connection,
    *,
    session_id: str,
    client_id: str,
    consultant_id: Optional[str],
    profile_name: str,
    channel_name: str,
    started_at: str,
    ended_at: str,
    duration_seconds: int,
    status: str,
    summary_storage_key: Optional[str],
    biomarker_storage_key: Optional[str],
    memory_storage_key: Optional[str],
    urgent_escalation: int,
    escalation_reason: str,
) -> None:
    db.execute(
        """
        INSERT INTO sessions (
            id, client_id, consultant_id, profile_name, channel_name,
            started_at, ended_at, duration_seconds, status,
            summary_storage_key, biomarker_storage_key, memory_storage_key,
            urgent_escalation, escalation_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            client_id=excluded.client_id,
            consultant_id=excluded.consultant_id,
            profile_name=excluded.profile_name,
            channel_name=excluded.channel_name,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            duration_seconds=excluded.duration_seconds,
            status=excluded.status,
            summary_storage_key=excluded.summary_storage_key,
            biomarker_storage_key=excluded.biomarker_storage_key,
            memory_storage_key=excluded.memory_storage_key,
            urgent_escalation=excluded.urgent_escalation,
            escalation_reason=excluded.escalation_reason
        """,
        (
            session_id,
            client_id,
            consultant_id,
            profile_name,
            channel_name,
            started_at,
            ended_at,
            duration_seconds,
            status,
            summary_storage_key,
            biomarker_storage_key,
            memory_storage_key,
            urgent_escalation,
            escalation_reason,
        ),
    )


def resolve_client_identity(db: sqlite3.Connection, **hashes: str):
    clauses = []
    params = []
    for column in ("google_sub_hash", "email_hash", "normalized_name_hash", "phone_hash"):
        value = hashes.get(column)
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if not clauses:
        return None
    sql = f"""
        SELECT cai.client_id, cc.consultant_id, c.is_active
        FROM client_auth_identities cai
        JOIN clients c ON c.id = cai.client_id
        LEFT JOIN consultant_clients cc ON cc.client_id = cai.client_id
        WHERE ({' OR '.join(clauses)})
        ORDER BY cc.created_at DESC
        LIMIT 1
    """
    return db.execute(sql, params).fetchone()


def get_client_context(db: sqlite3.Connection, client_id: str):
    client = db.execute(
        """
        SELECT c.*, cc.consultant_id, co.name AS consultant_name, co.email AS consultant_email
        FROM clients c
        LEFT JOIN consultant_clients cc ON cc.client_id = c.id
        LEFT JOIN consultants co ON co.id = cc.consultant_id
        WHERE c.id = ?
        ORDER BY cc.created_at DESC
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    if not client:
        return None
    alerts = db.execute(
        """
        SELECT id, severity, source, title, created_at
        FROM session_alerts
        WHERE client_id = ? AND acknowledged_at IS NULL
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (client_id,),
    ).fetchall()
    return client, alerts


def list_clients_for_consultant(db: sqlite3.Connection, consultant_id: str):
    return db.execute(
        """
        SELECT c.*,
               COUNT(DISTINCT s.id) AS session_count,
               MAX(s.ended_at) AS last_session_at
        FROM clients c
        JOIN consultant_clients cc ON cc.client_id = c.id
        LEFT JOIN sessions s ON s.client_id = c.id
        WHERE cc.consultant_id = ? AND c.is_active = 1
        GROUP BY c.id
        ORDER BY COALESCE(MAX(s.ended_at), c.created_at) DESC
        """,
        (consultant_id,),
    ).fetchall()


def get_client_detail(db: sqlite3.Connection, client_id: str, consultant_id: Optional[str] = None):
    params: List[str] = [client_id]
    consultant_sql = ""
    if consultant_id:
        consultant_sql = "AND EXISTS (SELECT 1 FROM consultant_clients cc WHERE cc.client_id = c.id AND cc.consultant_id = ?)"
        params.append(consultant_id)
    return db.execute(
        f"""
        SELECT c.*,
               co.id AS consultant_id,
               co.name AS consultant_name,
               co.email AS consultant_email
        FROM clients c
        LEFT JOIN consultant_clients cc ON cc.client_id = c.id
        LEFT JOIN consultants co ON co.id = cc.consultant_id
        WHERE c.id = ?
        {consultant_sql}
        ORDER BY cc.created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def list_sessions(db: sqlite3.Connection, consultant_id: Optional[str] = None, limit: int = 100):
    if consultant_id:
        return db.execute(
            """
            SELECT s.*, c.display_name, c.email, c.phone_number
            FROM sessions s
            JOIN clients c ON c.id = s.client_id
            WHERE s.consultant_id = ?
            ORDER BY COALESCE(s.ended_at, s.started_at, s.created_at) DESC
            LIMIT ?
            """,
            (consultant_id, limit),
        ).fetchall()
    return db.execute(
        """
        SELECT s.*, c.display_name, c.email, c.phone_number, co.name AS consultant_name
        FROM sessions s
        JOIN clients c ON c.id = s.client_id
        LEFT JOIN consultants co ON co.id = s.consultant_id
        ORDER BY COALESCE(s.ended_at, s.started_at, s.created_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def list_sessions_for_client(db: sqlite3.Connection, client_id: str, limit: int = 50):
    return db.execute(
        """
        SELECT s.*
        FROM sessions s
        WHERE s.client_id = ?
        ORDER BY COALESCE(s.ended_at, s.started_at, s.created_at) DESC
        LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()


def get_session_detail(db: sqlite3.Connection, session_id: str, consultant_id: Optional[str] = None):
    params: List[str] = [session_id]
    consultant_sql = ""
    if consultant_id:
        consultant_sql = "AND s.consultant_id = ?"
        params.append(consultant_id)
    return db.execute(
        f"""
        SELECT s.*, c.display_name, c.email, c.phone_number, co.name AS consultant_name
        FROM sessions s
        JOIN clients c ON c.id = s.client_id
        LEFT JOIN consultants co ON co.id = s.consultant_id
        WHERE s.id = ?
        {consultant_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()


def create_session_alert(
    db: sqlite3.Connection,
    *,
    session_id: str,
    client_id: str,
    severity: str,
    source: str,
    title: str,
    details_storage_key: str = "",
) -> None:
    db.execute(
        """
        INSERT INTO session_alerts (
            session_id, client_id, severity, source, title, details_storage_key
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            client_id,
            severity,
            source,
            title,
            details_storage_key or None,
        ),
    )
