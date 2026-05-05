# HTTP Interfaces

This deep dive keeps the exhaustive route and payload detail out of L1.

## Web Routes

Consultant routes:

- `GET/POST /consultant/login`
- `GET/POST /consultant/verify`
- `GET/POST /consultant/account`
- `GET /consultant/dashboard`
- `GET/POST /consultant/clients`
- `GET/POST /consultant/clients/<client_id>`
- `GET/POST /consultant/clients/<client_id>/meetings/new`
- `GET/POST /consultant/clients/<client_id>/messages/new`
- `POST /consultant/clients/<client_id>/messages/send`
- `GET /consultant/clients/<client_id>/messages/thread`
- `GET /consultant/meetings`
- `GET/POST /consultant/meetings/<meeting_id>`
- `GET /consultant/meetings/<meeting_id>/join`
- `GET /consultant/sessions`
- `GET/POST /consultant/sessions/<session_id>`

Admin routes:

- `GET/POST /admin/login`
- `GET/POST /admin/account`
- `GET /admin/dashboard`
- `GET/POST /admin/consultants`
- `GET/POST /admin/consultants/<consultant_id>`

Shared routes:

- `GET /`
- `GET /home`
- `GET/POST /meetings/respond/<token>`
- `GET/POST /client/messages/<token>`
- `GET /client/messages/<token>/thread`
- `POST /session-feedback`
- `POST /logout`
- `GET /health`

## Realtime Routes

- `GET /ws/consultant/clients/<client_id>/messages`
- `GET /ws/client/messages/<token>`

Realtime notes:

- consultant realtime requires an authenticated consultant session
- client realtime requires an unexpired secure access token
- realtime currently sends `thread_updated` notifications and the page re-fetches thread JSON

## Internal APIs

### `GET /internal/health`

No signature required. Returns service status and DB path.

### `GET /internal/resolve-client`

Signature required.

Accepted query params:

- `google_sub_hash`
- `email_hash`
- `normalized_name_hash`
- `phone_hash`

Returns:

- `found`
- `client_id`
- `consultant_id`
- `is_active`

### `GET /internal/client-context`

Signature required.

Required query params:

- `client_id`

Returns consultant-facing prompt context:

- `client_id`
- `display_name`
- `year_of_birth`
- `sex`
- `consultant_id`
- `consultant_name`
- `notes`
- `direction`
- `latest_summary`
- `ai_personal_summary`
- `human_personal_summary`
- `ai_session_count`
- `human_session_count`
- `recent_summaries`
- `baseline`
- `alerts`

### `POST /internal/authorize-meeting-join`

Signature required.

Accepted JSON body:

- host join:
  - `participant_role = "host"`
  - `meeting_id`
  - `consultant_id`
- guest join:
  - `participant_role = "guest"`
  - `response_access_link_id`
  - `meeting_id`

Returns on success:

- `ok`
- `meeting_id`
- `participant_role`
- `client_id`
- `consultant_id`
- `channel_name`
- `participant_uid`
- `user_uid`
- `host_uid`
- `guest_uid`
- `rtm_uid`
- `scheduled_start_at`
- `scheduled_end_at`
- `join_window_start_at`
- `join_window_end_at`
- `ensure_meeting_services`

Rules:

- host join is allowed from `scheduled`, `client_viewed`, `accepted`, or `in_progress`
- guest join requires `accepted` or `in_progress`
- `simple-backend` must use the server response as authoritative

### `POST /internal/verify-client-password`

Signature required.

Required JSON body:

- `email`
- `password`

Returns on success:

- `ok`
- `client_id`
- `consultant_id`
- `display_name`
- `email`
- `phone_number`
- `is_active`

### `POST /internal/run-reminders`

Signature required.

Returns:

- `ok`
- `sent_24h`
- `sent_1m`
- `failed_24h`
- `failed_1m`
- `skipped_immediate`

### `POST /internal/session-complete`

Signature required.

Current accepted payload fields:

- `client_id`
- `session_id`
- `consultant_id`
- `session_kind`
- `meeting_id`
- `profile`
- `channel`
- `started_at`
- `ended_at`
- `duration_seconds`
- `status`
- `summary`
- `ai_personal_summary`
- `human_personal_summary`
- `biomarkers`
- `memory_storage_key`
- `transcript`
- `alerts`

`summary` current expected shape:

- `key_point_summary`
  - `headline`
  - `body`
- `brief_overview`
- `full_summary`
- `biomarker_summary`
- `risk_overview`
- `follow_up`
- `source`

Client key point summary shape (`ai_personal_summary` / `human_personal_summary`):

- `key_point_summary`
  - `headline`
  - `body`
- `brief_overview`
- `full_summary`

Persisted biomarker artifact notes:

- dashboard stores the posted biomarker payload under `clients/{client_id}/sessions/{session_id}/biomarkers.json.enc`
- during ingestion it may add dashboard-derived fields before encrypting:
  - `history_averages`
  - `history_window_sessions`
- these snapshot the client’s prior-session biomarker averages at the time the session ended so old session pages do not drift when newer sessions are added
- `source`

Current behavior:

- dashboard stores `summary` under the session artifact key
- dashboard stores `ai_personal_summary` or `human_personal_summary` directly on the client artifact key for that session type
- dashboard still recomputes:
  - session counts
  - biomarker baseline
  - latest safety snapshot
- dashboard does not re-author AI/human client summaries locally during normal ingestion

### `POST /session-feedback`

Client-authenticated public route.

Required JSON body:

- `session_id`
- `rating` (`1..5`)

Optional JSON body:

- `comment`
- `avatar_id`

Current behavior:

- if the session row already exists, dashboard writes directly to `session_feedback`
- if the session row does not exist yet, dashboard stages the row in `pending_session_feedback`
- `/internal/session-complete` later claims and merges that pending row onto the real session
- staged feedback is discarded when the pending `client_id` does not match the completed session `client_id`

### `POST /internal/crisis-escalate-init`

Signature required.

Required JSON body:

- `client_id`
- `session_id`
- `level` (numeric)

Optional / conditional JSON body:

- `meeting_id`
- `channel_name` required when `meeting_id` is absent
- `alert`
- `source`

Returns:

- `ok`
- `escalate`
- `escalation_event_id`
- if `escalate=true`:
  - `meeting_id`
  - `channel_name`
  - `client_id`
  - `client_display_name`
  - `escalation_phone_number`
  - `from_phone`
  - `sip_gateway`
  - `region`
  - `pstn_uid`
  - `rtc_token`
- if `escalate=false`:
  - `reason` (currently `missing_phone`)

### `POST /internal/crisis-escalate-status`

Signature required.

Required JSON body:

- `escalation_event_id`
- `phase`

Optional JSON body:

- `reason`
- `provider_result`
- `client_announcement_text`
- `recipient_summary_text`
- `session_id`

Allowed `phase` values:

- `dialing`
- `answered`
- `failed`
- `completed`

Validation notes:

- invalid `phase` returns `400`
- missing `escalation_event_id` returns `404`
