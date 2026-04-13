# 06 Interfaces

Purpose: document the repo’s boundary contracts and stable interfaces.

## Web Interfaces

Consultant routes:

- `GET/POST /consultant/login`
- `GET/POST /consultant/verify`
- `GET /consultant/dashboard`
- `GET/POST /consultant/clients`
- `GET /consultant/clients/<client_id>`
- `GET /consultant/sessions`
- `GET /consultant/sessions/<session_id>`

Admin routes:

- `GET/POST /admin/login`
- `GET /admin/dashboard`
- `GET/POST /admin/consultants`

Shared routes:

- `GET /`
- `GET /home`
- `POST /logout`
- `GET /health`

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

Returns:

- `found`
- `client_id`
- `consultant_id`
- `is_active`

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

Covered by:

- `tests/test_internal_api.py`
- `tests/test_smoke.py`

## CLI Interface

Commands in `run.py`:

- `serve`
- `init-db`
- `hash-password --password ...`
- `create-consultant --email --name --phone --password [--notification-email] [--escalation-phone-number]`
- `create-client --consultant-id --name [--email] [--phone] [--notification-email] [--escalation-phone-number] [--notes] [--direction]`
- `link-client-auth --client-id [--google-sub] [--email] [--name] [--phone]`

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
