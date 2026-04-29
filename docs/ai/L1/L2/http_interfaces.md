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
- `consultant_id`
- `consultant_name`
- `notes`
- `direction`
- `latest_summary`
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
