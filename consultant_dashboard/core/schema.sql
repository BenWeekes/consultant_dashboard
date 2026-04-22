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
);

CREATE TABLE IF NOT EXISTS consultants (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    notification_email TEXT NOT NULL,
    escalation_phone_number TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_consultants_vendor_email
ON consultants(vendor_id, email);

CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT,
    phone_number TEXT,
    notification_email TEXT,
    escalation_phone_number TEXT,
    notes_current TEXT,
    direction_current TEXT,
    created_by_consultant_id TEXT,
    latest_summary_storage_key TEXT,
    baseline_storage_key TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_consultant_id) REFERENCES consultants(id),
    FOREIGN KEY (vendor_id) REFERENCES vendors(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_vendor_email
ON clients(vendor_id, email);

CREATE TABLE IF NOT EXISTS consultant_clients (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    consultant_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'primary',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (consultant_id) REFERENCES consultants(id) ON DELETE CASCADE,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
    UNIQUE (consultant_id, client_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_consultant_clients_client_id
ON consultant_clients(client_id);

CREATE TABLE IF NOT EXISTS client_auth_identities (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    google_sub_hash TEXT,
    email_hash TEXT,
    normalized_name_hash TEXT,
    phone_hash TEXT,
    last_verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    consultant_id TEXT,
    session_kind TEXT NOT NULL DEFAULT 'avatar_ai_session',
    meeting_id TEXT,
    transcription_enabled INTEGER NOT NULL DEFAULT 0,
    audio_biomarkers_enabled INTEGER NOT NULL DEFAULT 1,
    video_biomarkers_enabled INTEGER NOT NULL DEFAULT 1,
    profile_name TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    summary_storage_key TEXT,
    transcript_storage_key TEXT,
    biomarker_storage_key TEXT,
    memory_storage_key TEXT,
    urgent_escalation INTEGER NOT NULL DEFAULT 0,
    escalation_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (consultant_id) REFERENCES consultants(id)
);

CREATE TABLE IF NOT EXISTS scheduled_meetings (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
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
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
    FOREIGN KEY (consultant_id) REFERENCES consultants(id) ON DELETE CASCADE,
    FOREIGN KEY (response_access_link_id) REFERENCES client_access_links(id) ON DELETE RESTRICT,
    FOREIGN KEY (linked_session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS meeting_events (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    meeting_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (meeting_id) REFERENCES scheduled_meetings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scheduled_meetings_channel_name
ON scheduled_meetings(channel_name);

CREATE TABLE IF NOT EXISTS session_alerts (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    details_storage_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS client_note_revisions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    field_type TEXT NOT NULL,
    content TEXT NOT NULL,
    edited_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS client_access_links (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_client_access_links_token_hash
ON client_access_links(token_hash);

CREATE TABLE IF NOT EXISTS client_messages (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    vendor_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    consultant_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'draft',
    delivery_error TEXT NOT NULL DEFAULT '',
    access_link_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    read_by_client_at TEXT,
    read_by_consultant_at TEXT,
    notification_pending INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
    FOREIGN KEY (consultant_id) REFERENCES consultants(id) ON DELETE CASCADE,
    FOREIGN KEY (access_link_id) REFERENCES client_access_links(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS client_policy (
    vendor_id TEXT NOT NULL,
    client_id TEXT PRIMARY KEY,
    consent_version TEXT,
    consent_captured_at TEXT,
    retention_policy TEXT,
    retention_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
