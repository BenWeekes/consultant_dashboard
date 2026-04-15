# 06 Interfaces

Purpose: document the repo’s boundary contracts and stable interfaces.

## Web Interfaces

Consultant routes:

- `GET/POST /consultant/login`
- `GET/POST /consultant/verify`
- `GET/POST /consultant/account`
- `GET /consultant/dashboard`
- `GET/POST /consultant/clients`
- `GET/POST /consultant/clients/<client_id>`
- `GET/POST /consultant/clients/<client_id>/messages/new`
- `POST /consultant/clients/<client_id>/messages/send`
- `GET /consultant/clients/<client_id>/messages/thread`
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
- `GET/POST /client/messages/<token>`
- `GET /client/messages/<token>/thread`
- `POST /logout`
- `GET /health`

Notes:

- consultants and admins have separate login URLs
- clients do not use normal dashboard logins, but they can open secure message links hosted by this service
- client authentication happens in `simple-backend`, then identity is resolved into this service over internal APIs
- client access is controlled by the client record email plus phone number, with optional client password support
- each client is assigned to one consultant at a time
- only US and UK phone numbers are supported right now
- the client overview page shows compact latest-session biomarker highlights and keeps the full grouped biomarker breakdown on the session detail page
- secure message links are valid for both the hosted reply page and the client realtime thread only until `expires_at`

Realtime routes:

- `GET /ws/consultant/clients/<client_id>/messages`
- `GET /ws/client/messages/<token>`

Realtime notes:

- consultant realtime requires an authenticated consultant session
- client realtime requires an unexpired secure access token
- realtime currently sends `thread_updated` notifications and the page re-fetches thread JSON

## Internal Service Interfaces

### `GET /internal/health`

No signature required.

Returns service status and DB path.

### `GET /internal/resolve-client`

Signature required.

Accepted query params:

- `google_sub_hash`
- `email_hash`
- `normalized_name_hash`
- `phone_hash`

Normal match expectation:

- `email_hash` + `phone_hash` are the main lookup pair for client authorization
- `google_sub_hash` and `normalized_name_hash` are supporting identity signals, not the primary requirement

Returns:

- `found`
- `client_id`
- `consultant_id`
- `is_active`

Normal caller:

- `simple-backend` after Google + phone verification
- when the profile requires dashboard-backed authorization, `simple-backend` should treat a missing match as access denied

### `GET /internal/client-context`

Signature required.

Required query params:

- `client_id`

Returns:

- consultant-facing client context fields:
  - `client_id`
  - `display_name`
  - `consultant_id`
  - `consultant_name`
  - `notes`
  - `direction`
  - `latest_summary`
  - `baseline`
  - `alerts`

Normal caller:

- `simple-backend`, which passes the returned context downstream to `server-custom-llm`

Influence on the next AI session:

- `notes` = durable background context
- `direction` = explicit steering for the next session
- `latest_summary` = previous generalized session summary
- `baseline` = biomarker averages from recent sessions
- `alerts` = open human follow-up signals
- `recent_summaries` are truncated in `simple-backend` before prompt injection to avoid consuming too much context window

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

Normal caller:

- `simple-backend` when a client uses email/password login before SMS verification

Implementation note:

- phone normalization currently exists in both `consultant-dashboard` and `simple-backend`
- behavior must stay aligned across both repos until that helper is extracted into shared code

### `POST /internal/session-complete`

Signature required.

Current accepted payload fields:

- `client_id`
- `session_id`
- `consultant_id`
- `profile`
- `channel`
- `started_at`
- `ended_at`
- `duration_seconds`
- `status`
- `summary`
- `biomarkers`
- `memory_storage_key`
- `urgent_escalation`
- `escalation_reason`
- `escalation_source`
- `alerts`

Effects:

- encrypts/stores summary and biomarker payloads
- upserts session metadata
- recomputes rolling biomarker baseline
- creates alert rows
- writes audit log

Normal caller:

- `server-custom-llm` after call-end summarization

Summary boundary:

- this endpoint is for consultant-facing generalized session summaries and biomarker aggregates
- live continuity memory for future AI sessions remains owned by `server-custom-llm`
- the dashboard stores all biomarker aggregates it receives, but the consultant UI should default to a compact subset and reveal the full grouped set on demand

Current `summary` object shape:

- `brief_overview`
- `overview`
- `full_summary`
- `biomarker_summary`
- `risk_overview`
- `follow_up`
- `source`

Covered by:

- `tests/test_internal_api.py`
- `tests/test_smoke.py`

## CLI Interface

Commands in `run.py`:

- `serve`
- `init-db`
- `hash-password --password ...`
- `create-consultant --email --name --phone --password [--notification-email] [--escalation-phone-number]`
- `create-client --consultant-id --name [--email] [--password] [--phone] [--notification-email] [--escalation-phone-number] [--notes] [--direction]`
- `link-client-auth --client-id [--google-sub] [--email] [--name] [--phone]`

The CLI helpers are operational tools and test fixtures. Normal client linking and password setup should happen through the dashboard UI plus the live auth flow in `simple-backend`.

Consultant password management:

- consultants can change their own password at `/consultant/account`
- admins can change their own password at `/admin/account`
- admins can set a consultant temporary password from `/admin/consultants/<consultant_id>`
- consultants can set or reset a client password from `/consultant/clients/<client_id>`
- client sign-in supports email/password + SMS, and can also support Google + SMS when the email matches the client record

## Messaging Interface

Web behavior:

- consultants send messages from the client detail page or `/consultant/clients/<client_id>/messages/new`
- client replies are collected at `/client/messages/<token>`
- outbound messages are stored even when delivery is not configured
- outbound message rows store `access_link_id`; they do not store the raw secure reply URL in SQLite metadata
- consultant and client thread views use JSON thread endpoints plus WebSocket notifications for live refresh

Delivery config:

- SendGrid:
  - `CONSULTANT_SENDGRID_API_KEY`
  - `CONSULTANT_EMAIL_FROM`
  - optional `CONSULTANT_EMAIL_REPLY_TO`
- Twilio Messaging:
  - `CONSULTANT_TWILIO_ACCOUNT_SID`
  - `CONSULTANT_TWILIO_AUTH_TOKEN`
  - `CONSULTANT_TWILIO_MESSAGING_SERVICE_SID` or `CONSULTANT_TWILIO_FROM_NUMBER`
- public reply-link host:
  - `CONSULTANT_PUBLIC_BASE_URL`

Current delivery policy:

- if the client has an email address, outbound notification prefers email
- otherwise, if the client has a phone number, outbound notification falls back to SMS
- otherwise the message is stored in the thread only

## Data Model Boundaries

Core tables:

- `consultants`
- `clients`
- `consultant_clients`
- `client_auth_identities`
- `sessions`
- `session_alerts`
- `client_note_revisions`
- `client_access_links`
- `client_messages`
- `client_policy`
- `audit_log`

Sensitive payloads are not fully stored inline in SQLite; SQLite stores references to encrypted blobs.

## Signature Contract

All protected internal requests use:

- `X-Consultant-Timestamp`
- `X-Consultant-Signature`

HMAC input:

```text
{timestamp}.{method}.{path}.{payload}
```

Time skew window:

- 300 seconds

## See Also

- [02 Architecture](02_architecture.md)
- [08 Security](08_security.md)
