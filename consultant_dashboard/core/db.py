import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .client_identity import build_identity_hashes


DEFAULT_VENDOR_SLUG = "mindfix"


def get_db(config: dict) -> sqlite3.Connection:
    db = sqlite3.connect(config["DB_PATH"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db(config: dict) -> None:
    db = get_db(config)
    schema_path = Path(__file__).with_name("schema.sql")
    existing_tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if existing_tables:
        _ensure_migrations(db, config)
    db.executescript(schema_path.read_text(encoding="utf-8"))
    _ensure_migrations(db, config)
    db.commit()
    db.close()


def _ensure_migrations(db: sqlite3.Connection, config: Optional[dict] = None) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            storage_root TEXT NOT NULL,
            www_root TEXT NOT NULL DEFAULT '',
            primary_host TEXT NOT NULL DEFAULT '',
            brand_config_json TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _seed_default_vendor(db, config)
    session_columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
    if "session_kind" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN session_kind TEXT NOT NULL DEFAULT 'avatar_ai_session'")
    if "meeting_id" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN meeting_id TEXT")
    if "transcript_storage_key" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN transcript_storage_key TEXT")
    if "transcription_enabled" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN transcription_enabled INTEGER NOT NULL DEFAULT 0")
    if "audio_biomarkers_enabled" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN audio_biomarkers_enabled INTEGER NOT NULL DEFAULT 1")
    if "video_biomarkers_enabled" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN video_biomarkers_enabled INTEGER NOT NULL DEFAULT 1")
    client_columns = {row["name"] for row in db.execute("PRAGMA table_info(clients)").fetchall()}
    consultant_columns = {row["name"] for row in db.execute("PRAGMA table_info(consultants)").fetchall()}
    default_vendor_id = _default_vendor_id(db, config)
    if "vendor_id" not in consultant_columns:
        db.execute("ALTER TABLE consultants ADD COLUMN vendor_id TEXT")
        db.execute("UPDATE consultants SET vendor_id = ? WHERE vendor_id IS NULL OR vendor_id = ''", (default_vendor_id,))
    if "vendor_id" not in client_columns:
        db.execute("ALTER TABLE clients ADD COLUMN vendor_id TEXT")
        db.execute("UPDATE clients SET vendor_id = ? WHERE vendor_id IS NULL OR vendor_id = ''", (default_vendor_id,))
    if "password_hash" not in client_columns:
        db.execute("ALTER TABLE clients ADD COLUMN password_hash TEXT")
    message_columns = {row["name"] for row in db.execute("PRAGMA table_info(client_messages)").fetchall()}
    consultant_client_columns = {row["name"] for row in db.execute("PRAGMA table_info(consultant_clients)").fetchall()}
    if "vendor_id" not in consultant_client_columns:
        db.execute("ALTER TABLE consultant_clients ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE consultant_clients
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM consultants WHERE consultants.id = consultant_clients.consultant_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    auth_identity_columns = {row["name"] for row in db.execute("PRAGMA table_info(client_auth_identities)").fetchall()}
    if "vendor_id" not in auth_identity_columns:
        db.execute("ALTER TABLE client_auth_identities ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE client_auth_identities
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = client_auth_identities.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    if "read_by_client_at" not in message_columns:
        db.execute("ALTER TABLE client_messages ADD COLUMN read_by_client_at TEXT")
    if "read_by_consultant_at" not in message_columns:
        db.execute("ALTER TABLE client_messages ADD COLUMN read_by_consultant_at TEXT")
    if "notification_pending" not in message_columns:
        db.execute("ALTER TABLE client_messages ADD COLUMN notification_pending INTEGER NOT NULL DEFAULT 0")
    if "notified_at" not in message_columns:
        db.execute("ALTER TABLE client_messages ADD COLUMN notified_at TEXT")
    if "vendor_id" not in message_columns:
        db.execute("ALTER TABLE client_messages ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE client_messages
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = client_messages.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_consultant_clients_client_id
        ON consultant_clients(client_id)
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_client_access_links_token_hash
        ON client_access_links(token_hash)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_meetings (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            client_id TEXT NOT NULL,
            consultant_id TEXT NOT NULL,
            meeting_type TEXT NOT NULL DEFAULT 'human',
            repeat_weekly INTEGER NOT NULL DEFAULT 0,
            transcription_enabled INTEGER NOT NULL DEFAULT 0,
            audio_biomarkers_enabled INTEGER NOT NULL DEFAULT 1,
            video_biomarkers_enabled INTEGER NOT NULL DEFAULT 1,
            transcription_provider TEXT NOT NULL DEFAULT '',
            transcription_language TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'scheduled',
            title TEXT NOT NULL,
            invite_message TEXT NOT NULL DEFAULT '',
            timezone_name TEXT NOT NULL,
            scheduled_start_at TEXT NOT NULL,
            scheduled_end_at TEXT NOT NULL,
            join_window_start_at TEXT NOT NULL,
            join_window_end_at TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            response_access_link_id TEXT NOT NULL UNIQUE,
            invite_delivery_status TEXT NOT NULL DEFAULT 'pending',
            invite_delivery_error TEXT NOT NULL DEFAULT '',
            reminder_24h_sent_at TEXT,
            reminder_1m_sent_at TEXT,
            accepted_at TEXT,
            declined_at TEXT,
            cancelled_at TEXT,
            in_progress_at TEXT,
            completed_at TEXT,
            client_joined_at TEXT,
            client_left_at TEXT,
            consultant_joined_at TEXT,
            consultant_left_at TEXT,
            attendance_outcome TEXT NOT NULL DEFAULT '',
            ended_by_role TEXT NOT NULL DEFAULT '',
            ended_by_id TEXT NOT NULL DEFAULT '',
            summary_storage_key TEXT,
            biomarker_storage_key TEXT,
            linked_session_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
            FOREIGN KEY (consultant_id) REFERENCES consultants(id) ON DELETE CASCADE,
            FOREIGN KEY (response_access_link_id) REFERENCES client_access_links(id) ON DELETE RESTRICT,
            FOREIGN KEY (linked_session_id) REFERENCES sessions(id) ON DELETE SET NULL
        )
        """
    )
    meeting_columns = {row["name"] for row in db.execute("PRAGMA table_info(scheduled_meetings)").fetchall()}
    if "vendor_id" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE scheduled_meetings
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = scheduled_meetings.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    if "meeting_type" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN meeting_type TEXT NOT NULL DEFAULT 'human'")
    if "repeat_weekly" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN repeat_weekly INTEGER NOT NULL DEFAULT 0")
    if "transcription_enabled" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN transcription_enabled INTEGER NOT NULL DEFAULT 0")
    if "audio_biomarkers_enabled" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN audio_biomarkers_enabled INTEGER NOT NULL DEFAULT 1")
    if "video_biomarkers_enabled" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN video_biomarkers_enabled INTEGER NOT NULL DEFAULT 1")
    if "transcription_provider" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN transcription_provider TEXT NOT NULL DEFAULT ''")
    if "transcription_language" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN transcription_language TEXT NOT NULL DEFAULT ''")
    if "reminder_24h_sent_at" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN reminder_24h_sent_at TEXT")
    if "reminder_1m_sent_at" not in meeting_columns:
        db.execute("ALTER TABLE scheduled_meetings ADD COLUMN reminder_1m_sent_at TEXT")
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scheduled_meetings_channel_name
        ON scheduled_meetings(channel_name)
        """
    )
    _ensure_scheduled_meetings_channel_not_unique(db)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_events (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            vendor_id TEXT,
            meeting_id TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (meeting_id) REFERENCES scheduled_meetings(id) ON DELETE CASCADE
        )
        """
    )
    meeting_event_columns = {row["name"] for row in db.execute("PRAGMA table_info(meeting_events)").fetchall()}
    if "vendor_id" not in meeting_event_columns:
        db.execute("ALTER TABLE meeting_events ADD COLUMN vendor_id TEXT")
    db.execute(
        """
        UPDATE meeting_events
        SET vendor_id = COALESCE(
            (SELECT vendor_id FROM scheduled_meetings WHERE scheduled_meetings.id = meeting_events.meeting_id),
            ?
        )
        WHERE vendor_id IS NULL OR vendor_id = ''
        """,
        (default_vendor_id,),
    )
    access_link_columns = {row["name"] for row in db.execute("PRAGMA table_info(client_access_links)").fetchall()}
    if "vendor_id" not in access_link_columns:
        db.execute("ALTER TABLE client_access_links ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE client_access_links
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = client_access_links.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    session_alert_columns = {row["name"] for row in db.execute("PRAGMA table_info(session_alerts)").fetchall()}
    if "vendor_id" not in session_alert_columns:
        db.execute("ALTER TABLE session_alerts ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE session_alerts
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = session_alerts.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    note_columns = {row["name"] for row in db.execute("PRAGMA table_info(client_note_revisions)").fetchall()}
    if "vendor_id" not in note_columns:
        db.execute("ALTER TABLE client_note_revisions ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE client_note_revisions
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = client_note_revisions.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    policy_columns = {row["name"] for row in db.execute("PRAGMA table_info(client_policy)").fetchall()}
    if "vendor_id" not in policy_columns:
        db.execute("ALTER TABLE client_policy ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE client_policy
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = client_policy.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )
    if "vendor_id" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN vendor_id TEXT")
        db.execute(
            """
            UPDATE sessions
            SET vendor_id = COALESCE(
                (SELECT vendor_id FROM clients WHERE clients.id = sessions.client_id),
                ?
            )
            WHERE vendor_id IS NULL OR vendor_id = ''
            """,
            (default_vendor_id,),
        )


def _seed_default_vendor(db: sqlite3.Connection, config: Optional[dict] = None) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    legacy_storage = str(repo_root / "shared-therapy-storage")
    legacy_www = str(repo_root / "consultant_dashboard" / "www" / DEFAULT_VENDOR_SLUG)
    default_storage = (config or {}).get("STORAGE_ROOT", legacy_storage)
    default_www = str(
        Path((config or {}).get("WWW_ROOT", str(repo_root / "consultant_dashboard" / "www"))) / DEFAULT_VENDOR_SLUG
    )
    default_host = (config or {}).get("PUBLIC_BASE_URL", "")
    existing = db.execute(
        "SELECT id, storage_root, www_root, primary_host FROM vendors WHERE slug = ?",
        (DEFAULT_VENDOR_SLUG,),
    ).fetchone()
    if existing:
        db.execute(
            """
            UPDATE vendors
            SET storage_root = CASE
                    WHEN storage_root = '' OR storage_root = ? THEN ?
                    ELSE storage_root
                END,
                www_root = CASE
                    WHEN www_root = '' OR www_root = ? THEN ?
                    ELSE www_root
                END,
                primary_host = CASE
                    WHEN primary_host = '' OR primary_host = ? THEN ?
                    ELSE primary_host
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                legacy_storage,
                default_storage,
                legacy_www,
                default_www,
                "",
                default_host,
                existing["id"],
            ),
        )
        return
    db.execute(
        """
        INSERT INTO vendors (slug, name, storage_root, www_root, primary_host, brand_config_json, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (
            DEFAULT_VENDOR_SLUG,
            "MindFix",
            default_storage,
            default_www,
            default_host,
            json.dumps({"name": "MindFix", "slug": DEFAULT_VENDOR_SLUG}),
        ),
    )


def _default_vendor_id(db: sqlite3.Connection, config: Optional[dict] = None) -> str:
    row = db.execute("SELECT id FROM vendors WHERE slug = ?", (DEFAULT_VENDOR_SLUG,)).fetchone()
    if row is None:
        _seed_default_vendor(db, config)
        row = db.execute("SELECT id FROM vendors WHERE slug = ?", (DEFAULT_VENDOR_SLUG,)).fetchone()
    return row["id"]


def list_vendors(db: sqlite3.Connection):
    return db.execute(
        """
        SELECT *
        FROM vendors
        WHERE is_active = 1
        ORDER BY created_at DESC
        """
    ).fetchall()


def get_vendor_by_slug(db: sqlite3.Connection, slug: str):
    return db.execute(
        """
        SELECT *
        FROM vendors
        WHERE slug = ? AND is_active = 1
        LIMIT 1
        """,
        ((slug or DEFAULT_VENDOR_SLUG).strip().lower(),),
    ).fetchone()


def get_vendor_by_id(db: sqlite3.Connection, vendor_id: str):
    return db.execute(
        """
        SELECT *
        FROM vendors
        WHERE id = ? AND is_active = 1
        LIMIT 1
        """,
        (vendor_id,),
    ).fetchone()


def create_vendor(
    db: sqlite3.Connection,
    *,
    slug: str,
    name: str,
    storage_root: str,
    www_root: str,
    primary_host: str = "",
    brand_config_json: str = "",
) -> str:
    db.execute(
        """
        INSERT INTO vendors (slug, name, storage_root, www_root, primary_host, brand_config_json, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (
            slug.strip().lower(),
            name.strip(),
            storage_root.strip(),
            www_root.strip(),
            primary_host.strip(),
            brand_config_json.strip(),
        ),
    )
    return db.execute("SELECT id FROM vendors WHERE rowid = last_insert_rowid()").fetchone()["id"]


def update_vendor(
    db: sqlite3.Connection,
    *,
    vendor_id: str,
    name: str,
    storage_root: str,
    www_root: str,
    primary_host: str = "",
    brand_config_json: str = "",
) -> None:
    db.execute(
        """
        UPDATE vendors
        SET name = ?,
            storage_root = ?,
            www_root = ?,
            primary_host = ?,
            brand_config_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            name.strip(),
            storage_root.strip(),
            www_root.strip(),
            primary_host.strip(),
            brand_config_json.strip(),
            vendor_id,
        ),
    )


def _vendor_id_for_consultant(db: sqlite3.Connection, consultant_id: str) -> str:
    row = db.execute("SELECT vendor_id FROM consultants WHERE id = ? LIMIT 1", (consultant_id,)).fetchone()
    return (row["vendor_id"] if row and row["vendor_id"] else _default_vendor_id(db))


def _vendor_id_for_client(db: sqlite3.Connection, client_id: str) -> str:
    row = db.execute("SELECT vendor_id FROM clients WHERE id = ? LIMIT 1", (client_id,)).fetchone()
    return (row["vendor_id"] if row and row["vendor_id"] else _default_vendor_id(db))


def _vendor_id_for_meeting(db: sqlite3.Connection, meeting_id: str) -> str:
    row = db.execute("SELECT vendor_id FROM scheduled_meetings WHERE id = ? LIMIT 1", (meeting_id,)).fetchone()
    return (row["vendor_id"] if row and row["vendor_id"] else _default_vendor_id(db))


def _ensure_scheduled_meetings_channel_not_unique(db: sqlite3.Connection) -> None:
    row = db.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'scheduled_meetings'
        """
    ).fetchone()
    sql = (row["sql"] or "") if row else ""
    if "channel_name TEXT NOT NULL UNIQUE" not in sql:
        return

    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        """
        CREATE TABLE scheduled_meetings_new (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            client_id TEXT NOT NULL,
            consultant_id TEXT NOT NULL,
            meeting_type TEXT NOT NULL DEFAULT 'human',
            repeat_weekly INTEGER NOT NULL DEFAULT 0,
            transcription_enabled INTEGER NOT NULL DEFAULT 0,
            audio_biomarkers_enabled INTEGER NOT NULL DEFAULT 1,
            video_biomarkers_enabled INTEGER NOT NULL DEFAULT 1,
            transcription_provider TEXT NOT NULL DEFAULT '',
            transcription_language TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'scheduled',
            title TEXT NOT NULL,
            invite_message TEXT NOT NULL DEFAULT '',
            timezone_name TEXT NOT NULL,
            scheduled_start_at TEXT NOT NULL,
            scheduled_end_at TEXT NOT NULL,
            join_window_start_at TEXT NOT NULL,
            join_window_end_at TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            response_access_link_id TEXT NOT NULL UNIQUE,
            invite_delivery_status TEXT NOT NULL DEFAULT 'pending',
            invite_delivery_error TEXT NOT NULL DEFAULT '',
            reminder_24h_sent_at TEXT,
            reminder_1m_sent_at TEXT,
            accepted_at TEXT,
            declined_at TEXT,
            cancelled_at TEXT,
            in_progress_at TEXT,
            completed_at TEXT,
            client_joined_at TEXT,
            client_left_at TEXT,
            consultant_joined_at TEXT,
            consultant_left_at TEXT,
            attendance_outcome TEXT NOT NULL DEFAULT '',
            ended_by_role TEXT NOT NULL DEFAULT '',
            ended_by_id TEXT NOT NULL DEFAULT '',
            summary_storage_key TEXT,
            biomarker_storage_key TEXT,
            linked_session_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
            FOREIGN KEY (consultant_id) REFERENCES consultants(id) ON DELETE CASCADE,
            FOREIGN KEY (response_access_link_id) REFERENCES client_access_links(id) ON DELETE RESTRICT,
            FOREIGN KEY (linked_session_id) REFERENCES sessions(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO scheduled_meetings_new (
            id, client_id, consultant_id, meeting_type, repeat_weekly, transcription_enabled, audio_biomarkers_enabled, video_biomarkers_enabled, transcription_provider, transcription_language, status, title, invite_message, timezone_name,
            scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
            channel_name, response_access_link_id, invite_delivery_status, invite_delivery_error,
            reminder_24h_sent_at, reminder_1m_sent_at,
            accepted_at, declined_at, cancelled_at, in_progress_at, completed_at,
            client_joined_at, client_left_at, consultant_joined_at, consultant_left_at,
            attendance_outcome, ended_by_role, ended_by_id, summary_storage_key,
            biomarker_storage_key, linked_session_id, created_at, updated_at
        )
        SELECT
            id, client_id, consultant_id, 'human', 0, 0, 1, 1, '', '', status, title, invite_message, timezone_name,
            scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
            channel_name, response_access_link_id, invite_delivery_status, invite_delivery_error,
            NULL, NULL,
            accepted_at, declined_at, cancelled_at, in_progress_at, completed_at,
            client_joined_at, client_left_at, consultant_joined_at, consultant_left_at,
            attendance_outcome, ended_by_role, ended_by_id, summary_storage_key,
            biomarker_storage_key, linked_session_id, created_at, updated_at
        FROM scheduled_meetings
        """
    )
    db.execute("DROP TABLE scheduled_meetings")
    db.execute("ALTER TABLE scheduled_meetings_new RENAME TO scheduled_meetings")
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scheduled_meetings_channel_name
        ON scheduled_meetings(channel_name)
        """
    )
    db.execute("PRAGMA foreign_keys = ON")


def create_consultant(
    db: sqlite3.Connection,
    *,
    vendor_id: str = "",
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
            vendor_id, email, password_hash, name, phone_number,
            notification_email, escalation_phone_number, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            vendor_id or _default_vendor_id(db),
            email.lower().strip(),
            password_hash,
            name.strip(),
            phone_number.strip(),
            notification_email.strip(),
            escalation_phone_number.strip(),
        ),
    )


def get_consultant_by_email(db: sqlite3.Connection, email: str, vendor_id: str = ""):
    return db.execute(
        "SELECT * FROM consultants WHERE email = ? AND vendor_id = ? AND is_active = 1",
        (email.lower().strip(), vendor_id or _default_vendor_id(db)),
    ).fetchone()


def get_consultant_by_id(db: sqlite3.Connection, consultant_id: str, vendor_id: str = ""):
    params: List[object] = [consultant_id]
    vendor_sql = ""
    if vendor_id:
        vendor_sql = "AND vendor_id = ?"
        params.append(vendor_id)
    return db.execute(
        f"SELECT * FROM consultants WHERE id = ? {vendor_sql} AND is_active = 1",
        params,
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


def update_client_context(
    db: sqlite3.Connection,
    *,
    client_id: str,
    notes: str,
    direction: str,
) -> None:
    db.execute(
        """
        UPDATE clients
        SET notes_current = ?,
            direction_current = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (notes.strip(), direction.strip(), client_id),
    )


def deactivate_client(
    db: sqlite3.Connection,
    *,
    client_id: str,
) -> None:
    db.execute(
        """
        UPDATE clients
        SET is_active = 0, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (client_id,),
    )


def delete_session(
    db: sqlite3.Connection,
    *,
    session_id: str,
) -> None:
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def get_latest_session_artifacts(db: sqlite3.Connection, client_id: str):
    return db.execute(
        """
        SELECT summary_storage_key, transcript_storage_key, biomarker_storage_key
        FROM sessions
        WHERE client_id = ?
        ORDER BY COALESCE(ended_at, started_at, created_at) DESC
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()


def list_recent_biomarker_keys(db: sqlite3.Connection, client_id: str, limit: int = 5):
    return db.execute(
        """
        SELECT biomarker_storage_key
        FROM sessions
        WHERE client_id = ? AND biomarker_storage_key IS NOT NULL
        ORDER BY COALESCE(ended_at, started_at, created_at) DESC
        LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()


def list_consultants(db: sqlite3.Connection, vendor_id: str = ""):
    params: List[object] = []
    vendor_sql = ""
    if vendor_id:
        vendor_sql = "AND c.vendor_id = ?"
        params.append(vendor_id)
    return db.execute(
        f"""
        SELECT c.*,
               COUNT(DISTINCT cc.client_id) AS client_count,
               COUNT(DISTINCT s.id) AS session_count
        FROM consultants c
        LEFT JOIN consultant_clients cc ON cc.consultant_id = c.id
        LEFT JOIN sessions s ON s.consultant_id = c.id
        WHERE c.is_active = 1
        {vendor_sql}
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """,
        params,
    ).fetchall()


def create_client(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    vendor_id: str = "",
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
            vendor_id, display_name, email, password_hash, phone_number, notification_email,
            escalation_phone_number, notes_current, direction_current,
            created_by_consultant_id, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            vendor_id or _vendor_id_for_consultant(db, consultant_id),
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
        INSERT OR REPLACE INTO consultant_clients (id, vendor_id, consultant_id, client_id, role, created_at)
        VALUES (
            COALESCE((SELECT id FROM consultant_clients WHERE client_id = ?), lower(hex(randomblob(16)))),
            ?, ?, ?, 'primary',
            COALESCE((SELECT created_at FROM consultant_clients WHERE client_id = ?), CURRENT_TIMESTAMP)
        )
        """,
        (
            client_id,
            vendor_id or _vendor_id_for_consultant(db, consultant_id),
            consultant_id,
            client_id,
            client_id,
        ),
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


def get_client_by_email(db: sqlite3.Connection, email: str, vendor_id: str = ""):
    return db.execute(
        """
        SELECT c.*, cc.consultant_id
        FROM clients c
        LEFT JOIN consultant_clients cc ON cc.client_id = c.id
        WHERE c.email = ? AND c.vendor_id = ? AND c.is_active = 1
        ORDER BY cc.created_at DESC
        LIMIT 1
        """,
        (email.lower().strip(), vendor_id or _default_vendor_id(db)),
    ).fetchone()


def create_client_access_link(
    db: sqlite3.Connection,
    *,
    client_id: str,
    created_by: str,
    token_hash: str,
    expires_at: str,
) -> str:
    db.execute(
        """
        INSERT INTO client_access_links (vendor_id, client_id, created_by, token_hash, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (_vendor_id_for_client(db, client_id), client_id, created_by, token_hash, expires_at),
    )
    return db.execute("SELECT id FROM client_access_links WHERE rowid = last_insert_rowid()").fetchone()["id"]


def get_client_access_link_by_hash(db: sqlite3.Connection, token_hash: str):
    return db.execute(
        """
        SELECT cal.*, c.display_name, c.email, c.phone_number, c.notification_email,
               co.id AS consultant_id, co.name AS consultant_name, co.email AS consultant_email
        FROM client_access_links cal
        JOIN clients c ON c.id = cal.client_id
        LEFT JOIN consultant_clients cc ON cc.client_id = c.id
        LEFT JOIN consultants co ON co.id = cc.consultant_id
        WHERE cal.token_hash = ? AND c.is_active = 1
        ORDER BY cal.created_at DESC
        LIMIT 1
        """,
        (token_hash,),
    ).fetchone()


def mark_client_access_link_used(db: sqlite3.Connection, link_id: str) -> None:
    db.execute(
        """
        UPDATE client_access_links
        SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP)
        WHERE id = ?
        """,
        (link_id,),
    )


def create_scheduled_meeting(
    db: sqlite3.Connection,
    *,
    client_id: str,
    consultant_id: str,
    meeting_type: str = "human",
    repeat_weekly: bool = False,
    transcription_enabled: bool = False,
    audio_biomarkers_enabled: bool = True,
    video_biomarkers_enabled: bool = True,
    transcription_provider: str = "",
    transcription_language: str = "",
    title: str,
    invite_message: str,
    timezone_name: str,
    scheduled_start_at: str,
    scheduled_end_at: str,
    join_window_start_at: str,
    join_window_end_at: str,
    channel_name: str,
    response_access_link_id: str,
) -> str:
    db.execute(
        """
        INSERT INTO scheduled_meetings (
            vendor_id, client_id, consultant_id, meeting_type, repeat_weekly,
            transcription_enabled, audio_biomarkers_enabled, video_biomarkers_enabled,
            transcription_provider, transcription_language,
            title, invite_message, timezone_name,
            scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
            channel_name, response_access_link_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _vendor_id_for_client(db, client_id),
            client_id,
            consultant_id,
            (meeting_type or "human").strip().lower(),
            1 if repeat_weekly else 0,
            1 if transcription_enabled else 0,
            1 if audio_biomarkers_enabled else 0,
            1 if video_biomarkers_enabled else 0,
            (transcription_provider or "").strip(),
            (transcription_language or "").strip(),
            title.strip(),
            invite_message.strip(),
            timezone_name.strip(),
            scheduled_start_at,
            scheduled_end_at,
            join_window_start_at,
            join_window_end_at,
            channel_name,
            response_access_link_id,
        ),
    )
    return db.execute(
        "SELECT id FROM scheduled_meetings WHERE rowid = last_insert_rowid()"
    ).fetchone()["id"]


def find_open_meeting_for_pair(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    client_id: str,
    meeting_type: str = "human",
):
    return db.execute(
        """
        SELECT *
        FROM scheduled_meetings
        WHERE consultant_id = ?
          AND client_id = ?
          AND meeting_type = ?
          AND status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
        ORDER BY COALESCE(in_progress_at, scheduled_start_at, created_at) DESC
        LIMIT 1
        """,
        (consultant_id, client_id, (meeting_type or "human").strip().lower()),
    ).fetchone()


def update_meeting_invite_delivery(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    delivery_status: str,
    delivery_error: str = "",
) -> None:
    db.execute(
        """
        UPDATE scheduled_meetings
        SET invite_delivery_status = ?,
            invite_delivery_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (delivery_status.strip(), delivery_error.strip(), meeting_id),
    )


def record_meeting_event(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    actor_type: str,
    actor_id: str,
    event_type: str,
    details: Optional[Dict] = None,
) -> None:
    db.execute(
        """
        INSERT INTO meeting_events (vendor_id, meeting_id, actor_type, actor_id, event_type, details_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _vendor_id_for_meeting(db, meeting_id),
            meeting_id,
            actor_type,
            actor_id,
            event_type,
            json.dumps(details or {}),
        ),
    )


def list_meetings_for_client(
    db: sqlite3.Connection,
    *,
    client_id: str,
    consultant_id: Optional[str] = None,
    limit: int = 50,
):
    params: List[object] = [client_id]
    consultant_sql = ""
    if consultant_id:
        consultant_sql = "AND sm.consultant_id = ?"
        params.append(consultant_id)
    params.append(limit)
    return db.execute(
        f"""
        SELECT sm.*, c.display_name AS client_name, co.name AS consultant_name
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        WHERE sm.client_id = ?
        {consultant_sql}
        ORDER BY
          CASE
            WHEN sm.status = 'in_progress' THEN 0
            WHEN sm.status IN ('scheduled', 'client_viewed', 'accepted') THEN 1
            WHEN sm.status = 'completed' THEN 2
            ELSE 3
          END,
          sm.scheduled_start_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def list_meetings_for_consultant(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    limit: int = 100,
):
    return db.execute(
        """
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        WHERE sm.consultant_id = ?
        ORDER BY
          CASE
            WHEN sm.status = 'in_progress' THEN 0
            WHEN sm.status IN ('scheduled', 'client_viewed', 'accepted') THEN 1
            WHEN sm.status = 'completed' THEN 2
            ELSE 3
          END,
          sm.scheduled_start_at DESC
        LIMIT ?
        """,
        (consultant_id, limit),
    ).fetchall()


def next_meeting_for_client(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    client_id: str,
):
    return db.execute(
        """
        SELECT *
        FROM scheduled_meetings
        WHERE consultant_id = ?
          AND client_id = ?
          AND status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
        ORDER BY
          CASE
            WHEN status = 'in_progress' THEN 0
            ELSE 1
          END,
          scheduled_start_at ASC
        LIMIT 1
        """,
        (consultant_id, client_id),
    ).fetchone()


def get_scheduled_meeting(db: sqlite3.Connection, meeting_id: str):
    return db.execute(
        """
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email,
               c.phone_number AS client_phone_number, c.notification_email AS client_notification_email,
               co.name AS consultant_name, co.email AS consultant_email, co.notification_email AS consultant_notification_email
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        WHERE sm.id = ?
        LIMIT 1
        """,
        (meeting_id,),
    ).fetchone()


def get_scheduled_meeting_detail(
    db: sqlite3.Connection,
    meeting_id: str,
    *,
    consultant_id: Optional[str] = None,
):
    params: List[object] = [meeting_id]
    consultant_sql = ""
    if consultant_id:
        consultant_sql = "AND sm.consultant_id = ?"
        params.append(consultant_id)
    return db.execute(
        f"""
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email,
               c.phone_number AS client_phone_number, c.notification_email AS client_notification_email,
               c.notes_current, c.direction_current,
               co.name AS consultant_name, co.email AS consultant_email, co.notification_email AS consultant_notification_email,
               s.status AS linked_session_status, s.summary_storage_key AS linked_summary_storage_key,
               s.biomarker_storage_key AS linked_biomarker_storage_key
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        LEFT JOIN sessions s ON s.id = sm.linked_session_id
        WHERE sm.id = ?
        {consultant_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()


def get_meeting_by_response_access_link_id(db: sqlite3.Connection, access_link_id: str):
    return db.execute(
        """
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email,
               c.phone_number AS client_phone_number,
               co.name AS consultant_name, co.email AS consultant_email
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        WHERE sm.response_access_link_id = ?
        LIMIT 1
        """,
        (access_link_id,),
    ).fetchone()


def get_client_access_link_by_id(db: sqlite3.Connection, link_id: str):
    return db.execute(
        "SELECT * FROM client_access_links WHERE id = ? LIMIT 1",
        (link_id,),
    ).fetchone()


def update_meeting_response_status(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    status: str,
) -> bool:
    if status == "accepted":
        cursor = db.execute(
            """
            UPDATE scheduled_meetings
            SET status = 'accepted',
                accepted_at = CURRENT_TIMESTAMP,
                declined_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('scheduled', 'client_viewed', 'accepted', 'declined')
            """,
            (meeting_id,),
        )
        return cursor.rowcount > 0
    if status == "declined":
        cursor = db.execute(
            """
            UPDATE scheduled_meetings
            SET status = 'declined',
                accepted_at = NULL,
                declined_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('scheduled', 'client_viewed', 'accepted', 'declined')
            """,
            (meeting_id,),
        )
        return cursor.rowcount > 0
    raise ValueError(f"Unsupported meeting response status: {status}")


def cancel_scheduled_meeting(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
) -> bool:
    cursor = db.execute(
        """
        UPDATE scheduled_meetings
        SET status = 'cancelled',
            cancelled_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status IN ('scheduled', 'client_viewed', 'accepted')
        """,
        (meeting_id,),
    )
    return cursor.rowcount > 0


def delete_scheduled_meeting(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
) -> bool:
    row = db.execute(
        """
        SELECT response_access_link_id, status
        FROM scheduled_meetings
        WHERE id = ?
        LIMIT 1
        """,
        (meeting_id,),
    ).fetchone()
    if not row or row["status"] == "in_progress":
        return False
    cursor = db.execute("DELETE FROM scheduled_meetings WHERE id = ?", (meeting_id,))
    if not cursor.rowcount:
        return False
    db.execute("DELETE FROM client_access_links WHERE id = ?", (row["response_access_link_id"],))
    return True


def _meeting_role_column(participant_role: str, *, joined: bool) -> str:
    normalized = (participant_role or "").strip().lower()
    mapping = {
        "guest": "client_joined_at" if joined else "client_left_at",
        "host": "consultant_joined_at" if joined else "consultant_left_at",
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported participant role: {participant_role}")
    return mapping[normalized]


def mark_meeting_joined(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    participant_role: str,
) -> Optional[bool]:
    role_column = _meeting_role_column(participant_role, joined=True)
    meeting = db.execute(
        """
        SELECT in_progress_at, status, scheduled_start_at, client_joined_at, consultant_joined_at
        FROM scheduled_meetings
        WHERE id = ?
        LIMIT 1
        """,
        (meeting_id,),
    ).fetchone()
    if not meeting or meeting["status"] not in {"scheduled", "client_viewed", "accepted", "in_progress"}:
        return None
    first_join = not meeting["client_joined_at"] and not meeting["consultant_joined_at"]
    start_at = datetime.fromisoformat((meeting["scheduled_start_at"] or "").replace("Z", "+00:00")) if meeting["scheduled_start_at"] else None
    should_mark_in_progress = (
        meeting["status"] == "in_progress"
        or participant_role == "guest"
        or bool(meeting["client_joined_at"])
        or bool(start_at and datetime.now(timezone.utc) >= start_at.astimezone(timezone.utc))
    )
    cursor = db.execute(
        f"""
        UPDATE scheduled_meetings
        SET status = CASE WHEN ? THEN 'in_progress' ELSE status END,
            in_progress_at = CASE WHEN ? THEN COALESCE(in_progress_at, CURRENT_TIMESTAMP) ELSE in_progress_at END,
            {role_column} = COALESCE({role_column}, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
        """,
        (1 if should_mark_in_progress else 0, 1 if should_mark_in_progress else 0, meeting_id),
    )
    if not cursor.rowcount:
        return None
    return first_join


def mark_meeting_participant_left(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    participant_role: str,
) -> None:
    role_column = _meeting_role_column(participant_role, joined=False)
    db.execute(
        f"""
        UPDATE scheduled_meetings
        SET {role_column} = COALESCE({role_column}, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (meeting_id,),
    )


def complete_scheduled_meeting(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    linked_session_id: str = "",
    summary_storage_key: str = "",
    biomarker_storage_key: str = "",
    attendance_outcome: str = "",
    ended_by_role: str = "",
    ended_by_id: str = "",
) -> bool:
    cursor = db.execute(
        """
        UPDATE scheduled_meetings
        SET status = 'completed',
            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
            linked_session_id = COALESCE(?, linked_session_id),
            summary_storage_key = COALESCE(?, summary_storage_key),
            biomarker_storage_key = COALESCE(?, biomarker_storage_key),
            attendance_outcome = CASE WHEN ? != '' THEN ? ELSE attendance_outcome END,
            ended_by_role = CASE WHEN ? != '' THEN ? ELSE ended_by_role END,
            ended_by_id = CASE WHEN ? != '' THEN ? ELSE ended_by_id END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress', 'completed')
        """,
        (
            linked_session_id or None,
            summary_storage_key or None,
            biomarker_storage_key or None,
            attendance_outcome,
            attendance_outcome,
            ended_by_role,
            ended_by_role,
            ended_by_id,
            ended_by_id,
            meeting_id,
        ),
    )
    return cursor.rowcount > 0


def mark_meeting_no_show(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    attendance_outcome: str,
) -> bool:
    cursor = db.execute(
        """
        UPDATE scheduled_meetings
        SET status = 'completed',
            attendance_outcome = ?,
            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
        """,
        (attendance_outcome, meeting_id),
    )
    return cursor.rowcount > 0


def list_active_meetings_for_reminders(db: sqlite3.Connection):
    return db.execute(
        """
        SELECT sm.*, c.display_name AS client_name, c.email AS client_email,
               c.phone_number AS client_phone_number,
               co.name AS consultant_name, co.email AS consultant_email
        FROM scheduled_meetings sm
        JOIN clients c ON c.id = sm.client_id
        JOIN consultants co ON co.id = sm.consultant_id
        WHERE sm.status IN ('scheduled', 'client_viewed', 'accepted')
        ORDER BY sm.scheduled_start_at ASC
        """
    ).fetchall()


def mark_meeting_reminder_sent(
    db: sqlite3.Connection,
    *,
    meeting_id: str,
    reminder_kind: str,
) -> bool:
    if reminder_kind == "24h":
        cursor = db.execute(
            """
            UPDATE scheduled_meetings
            SET reminder_24h_sent_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND reminder_24h_sent_at IS NULL
            """,
            (meeting_id,),
        )
        return cursor.rowcount > 0
    if reminder_kind == "1m":
        cursor = db.execute(
            """
            UPDATE scheduled_meetings
            SET reminder_1m_sent_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND reminder_1m_sent_at IS NULL
            """,
            (meeting_id,),
        )
        return cursor.rowcount > 0
    raise ValueError(f"Unsupported reminder kind: {reminder_kind}")


def has_overlapping_meeting(
    db: sqlite3.Connection,
    *,
    consultant_id: str,
    client_id: str,
    scheduled_start_at: str,
    scheduled_end_at: str,
) -> bool:
    row = db.execute(
        """
        SELECT id
        FROM scheduled_meetings
        WHERE status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
          AND (
            consultant_id = ?
            OR client_id = ?
          )
          AND scheduled_start_at < ?
          AND scheduled_end_at > ?
        LIMIT 1
        """,
        (consultant_id, client_id, scheduled_end_at, scheduled_start_at),
    ).fetchone()
    return bool(row)


def list_meeting_events(db: sqlite3.Connection, meeting_id: str):
    return db.execute(
        """
        SELECT *
        FROM meeting_events
        WHERE meeting_id = ?
        ORDER BY created_at ASC
        """,
        (meeting_id,),
    ).fetchall()


def create_client_message(
    db: sqlite3.Connection,
    *,
    client_id: str,
    consultant_id: str,
    direction: str,
    channel: str,
    subject: str,
    body: str,
    delivery_status: str,
    delivery_error: str = "",
    access_link_id: str = "",
    metadata: Optional[Dict] = None,
    read_by_client_at: str = "",
    read_by_consultant_at: str = "",
    notification_pending: int = 0,
    notified_at: str = "",
) -> str:
    db.execute(
        """
        INSERT INTO client_messages (
            vendor_id, client_id, consultant_id, direction, channel, subject, body,
            delivery_status, delivery_error, access_link_id, metadata_json,
            read_by_client_at, read_by_consultant_at, notification_pending, notified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _vendor_id_for_client(db, client_id),
            client_id,
            consultant_id,
            direction,
            channel,
            subject.strip(),
            body.strip(),
            delivery_status.strip(),
            delivery_error.strip(),
            access_link_id or None,
            json.dumps(metadata or {}),
            read_by_client_at or None,
            read_by_consultant_at or None,
            int(notification_pending),
            notified_at or None,
        ),
    )
    return db.execute("SELECT id FROM client_messages WHERE rowid = last_insert_rowid()").fetchone()["id"]


def list_client_messages(
    db: sqlite3.Connection,
    *,
    client_id: str,
    consultant_id: Optional[str] = None,
    limit: int = 100,
):
    params: List[object] = [client_id]
    consultant_sql = ""
    if consultant_id:
        consultant_sql = "AND m.consultant_id = ?"
        params.append(consultant_id)
    params.append(limit)
    return db.execute(
        f"""
        SELECT m.*, co.name AS consultant_name
        FROM client_messages m
        LEFT JOIN consultants co ON co.id = m.consultant_id
        WHERE m.client_id = ?
        {consultant_sql}
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def get_client_message(db: sqlite3.Connection, message_id: str):
    return db.execute(
        """
        SELECT m.*, co.name AS consultant_name, co.notification_email AS consultant_notification_email
        FROM client_messages m
        LEFT JOIN consultants co ON co.id = m.consultant_id
        WHERE m.id = ?
        """,
        (message_id,),
    ).fetchone()


def mark_client_messages_read(
    db: sqlite3.Connection,
    *,
    client_id: str,
    reader: str,
    consultant_id: str = "",
) -> int:
    if reader == "client":
        cursor = db.execute(
            """
            UPDATE client_messages
            SET read_by_client_at = COALESCE(read_by_client_at, CURRENT_TIMESTAMP)
            WHERE client_id = ? AND direction = 'outbound' AND read_by_client_at IS NULL
            """,
            (client_id,),
        )
        return cursor.rowcount
    if reader == "consultant":
        cursor = db.execute(
            """
            UPDATE client_messages
            SET read_by_consultant_at = COALESCE(read_by_consultant_at, CURRENT_TIMESTAMP)
            WHERE client_id = ? AND consultant_id = ? AND direction = 'inbound' AND read_by_consultant_at IS NULL
            """,
            (client_id, consultant_id),
        )
        return cursor.rowcount
    return 0


def mark_client_message_notification(
    db: sqlite3.Connection,
    *,
    message_id: str,
    delivery_status: str,
    delivery_error: str = "",
    notified: bool = False,
) -> None:
    db.execute(
        """
        UPDATE client_messages
        SET delivery_status = ?,
            delivery_error = ?,
            notification_pending = 0,
            notified_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE notified_at END
        WHERE id = ?
        """,
        (delivery_status.strip(), delivery_error.strip(), 1 if notified else 0, message_id),
    )


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
            SET vendor_id = COALESCE(vendor_id, ?),
                google_sub_hash = ?,
                email_hash = ?,
                normalized_name_hash = ?,
                phone_hash = ?,
                last_verified_at = CURRENT_TIMESTAMP
            WHERE client_id = ?
            """,
            (_vendor_id_for_client(db, client_id),) + params,
        )
        return
    db.execute(
        """
        INSERT INTO client_auth_identities (
            vendor_id, client_id, google_sub_hash, email_hash, normalized_name_hash, phone_hash, last_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            _vendor_id_for_client(db, client_id),
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
    session_kind: str,
    meeting_id: Optional[str],
    transcription_enabled: int,
    audio_biomarkers_enabled: int,
    video_biomarkers_enabled: int,
    profile_name: str,
    channel_name: str,
    started_at: str,
    ended_at: str,
    duration_seconds: int,
    status: str,
    summary_storage_key: Optional[str],
    transcript_storage_key: Optional[str],
    biomarker_storage_key: Optional[str],
    memory_storage_key: Optional[str],
    urgent_escalation: int,
    escalation_reason: str,
) -> None:
    db.execute(
        """
        INSERT INTO sessions (
            vendor_id, id, client_id, consultant_id, session_kind, meeting_id,
            transcription_enabled, audio_biomarkers_enabled, video_biomarkers_enabled,
            profile_name, channel_name,
            started_at, ended_at, duration_seconds, status,
            summary_storage_key, transcript_storage_key, biomarker_storage_key, memory_storage_key,
            urgent_escalation, escalation_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            vendor_id=excluded.vendor_id,
            client_id=excluded.client_id,
            consultant_id=excluded.consultant_id,
            session_kind=excluded.session_kind,
            meeting_id=excluded.meeting_id,
            transcription_enabled=excluded.transcription_enabled,
            audio_biomarkers_enabled=excluded.audio_biomarkers_enabled,
            video_biomarkers_enabled=excluded.video_biomarkers_enabled,
            profile_name=excluded.profile_name,
            channel_name=excluded.channel_name,
            started_at=excluded.started_at,
            ended_at=excluded.ended_at,
            duration_seconds=excluded.duration_seconds,
            status=excluded.status,
            summary_storage_key=excluded.summary_storage_key,
            transcript_storage_key=excluded.transcript_storage_key,
            biomarker_storage_key=excluded.biomarker_storage_key,
            memory_storage_key=excluded.memory_storage_key,
            urgent_escalation=excluded.urgent_escalation,
            escalation_reason=excluded.escalation_reason
        """,
        (
            _vendor_id_for_client(db, client_id),
            session_id,
            client_id,
            consultant_id,
            session_kind,
            meeting_id,
            transcription_enabled,
            audio_biomarkers_enabled,
            video_biomarkers_enabled,
            profile_name,
            channel_name,
            started_at,
            ended_at,
            duration_seconds,
            status,
            summary_storage_key,
            transcript_storage_key,
            biomarker_storage_key,
            memory_storage_key,
            urgent_escalation,
            escalation_reason,
        ),
    )


def resolve_client_identity(db: sqlite3.Connection, vendor_id: str = "", **hashes: str):
    clauses = []
    params: List[object] = []
    for column in ("google_sub_hash", "email_hash", "normalized_name_hash", "phone_hash"):
        value = hashes.get(column)
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if not clauses:
        return None
    vendor_sql = ""
    if vendor_id:
        vendor_sql = "AND cai.vendor_id = ?"
        params.append(vendor_id)
    sql = f"""
        SELECT cai.client_id, cc.consultant_id, c.is_active, c.email, c.display_name, c.phone_number
        FROM client_auth_identities cai
        JOIN clients c ON c.id = cai.client_id
        LEFT JOIN consultant_clients cc ON cc.client_id = cai.client_id
        WHERE ({' OR '.join(clauses)})
        {vendor_sql}
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
               (
                   SELECT COUNT(*)
                   FROM sessions s
                   WHERE s.client_id = c.id
               ) AS session_count,
               (
                   SELECT MAX(COALESCE(s.ended_at, s.started_at, s.created_at))
                   FROM sessions s
                   WHERE s.client_id = c.id
               ) AS last_session_at,
               (
                   SELECT s.id
                   FROM sessions s
                   WHERE s.client_id = c.id
                   ORDER BY COALESCE(s.ended_at, s.started_at, s.created_at) DESC
                   LIMIT 1
               ) AS last_session_id,
               (
                   SELECT COUNT(*)
                   FROM client_messages m
                   WHERE m.client_id = c.id
                     AND m.consultant_id = ?
                     AND m.direction = 'inbound'
                     AND m.read_by_consultant_at IS NULL
               ) AS unread_message_count,
               (
                   SELECT sm.scheduled_start_at
                   FROM scheduled_meetings sm
                   WHERE sm.client_id = c.id
                     AND sm.consultant_id = ?
                     AND sm.status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
                   ORDER BY
                     CASE WHEN sm.status = 'in_progress' THEN 0 ELSE 1 END,
                     sm.scheduled_start_at ASC
                   LIMIT 1
               ) AS next_meeting_at,
               (
                   SELECT sm.status
                   FROM scheduled_meetings sm
                   WHERE sm.client_id = c.id
                     AND sm.consultant_id = ?
                     AND sm.status IN ('scheduled', 'client_viewed', 'accepted', 'in_progress')
                   ORDER BY
                     CASE WHEN sm.status = 'in_progress' THEN 0 ELSE 1 END,
                     sm.scheduled_start_at ASC
                   LIMIT 1
               ) AS next_meeting_status
        FROM clients c
        JOIN consultant_clients cc ON cc.client_id = c.id
        WHERE cc.consultant_id = ? AND c.is_active = 1
        ORDER BY COALESCE(
            (
                SELECT MAX(COALESCE(s.ended_at, s.started_at, s.created_at))
                FROM sessions s
                WHERE s.client_id = c.id
            ),
            c.created_at
        ) DESC
        """,
        (consultant_id, consultant_id, consultant_id, consultant_id),
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
        SELECT s.*, c.display_name, c.email, c.phone_number, c.notes_current, c.direction_current,
               c.baseline_storage_key, co.name AS consultant_name
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
            vendor_id, session_id, client_id, severity, source, title, details_storage_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _vendor_id_for_client(db, client_id),
            session_id,
            client_id,
            severity,
            source,
            title,
            details_storage_key or None,
        ),
    )
