# 02 Architecture

Purpose: explain what this service owns, what it does not own, and how data flows through it.

## Role in the System

This repo is the product/admin service in a larger multi-service setup.

It owns:

- consultant/admin authentication
- client records and consultant-to-client assignment
- session index and therapist/consultant-facing review pages
- audit log storage
- encrypted storage references for summaries, biomarker aggregates, and alerts
- internal APIs for:
  - client resolution
  - start-of-session context fetch
  - end-of-call ingestion

It does not own:

- Agora session launch
- live call orchestration
- live memory generation during the call

Those stay in sibling services such as `simple-backend` and `server-custom-llm`.

## High-Level Component Model

```text
simple-backend ----signed GET----> consultant-dashboard /internal/resolve-client
simple-backend ----signed GET----> consultant-dashboard /internal/client-context
server-custom-llm -signed POST---> consultant-dashboard /internal/session-complete

consultant/admin browser ---> consultant-dashboard web routes

consultant-dashboard
  ├─ SQLite metadata
  ├─ encrypted filesystem artifact store
  └─ HTML dashboards
```

## Request Flows

### Consultant login

1. Consultant submits email/password.
2. Password is checked against `consultants.password_hash`.
3. OTP is sent through Twilio Verify if configured; otherwise dev mode stores/logs a local code.
4. `/consultant/verify` validates the code and creates the authenticated session.

### Admin login

1. Admin submits email/password.
2. Credentials are checked against `config/admin_auth.conf`.
3. Session is created directly; no separate OTP exists yet for admins.

### Start-of-session support

1. External caller sends signed `GET /internal/resolve-client` with hashed identity fields.
2. Service maps hashes to `client_id` in `client_auth_identities`.
3. External caller sends signed `GET /internal/client-context?client_id=...`.
4. Service returns current notes, direction, recent summary, baseline, and open alerts.

### End-of-call ingestion

1. `server-custom-llm` sends signed `POST /internal/session-complete`.
2. Service encrypts and stores summary/biomarker/alert payloads under `THERAPY_STORAGE_ROOT`.
3. Service upserts the `sessions` row.
4. Service recomputes the rolling biomarker baseline from the latest five stored biomarker snapshots.
5. Service stores session alerts and writes an audit log row.

## Storage Model

Two layers exist:

- relational metadata in SQLite
- encrypted JSON artifacts on disk

SQLite stores references like:

- `summary_storage_key`
- `biomarker_storage_key`
- `details_storage_key`
- `baseline_storage_key`

Artifacts live under a shared storage root, with keys such as:

- `clients/{client_id}/sessions/{session_id}/summary.json.enc`
- `clients/{client_id}/sessions/{session_id}/biomarkers.json.enc`
- `clients/{client_id}/sessions/{session_id}/alerts/{n}.json.enc`
- `clients/{client_id}/baseline.json.enc`

## Current Boundaries

Current implementation assumes:

- one service-local SQLite database
- local/shared filesystem storage
- HMAC-signed internal HTTP between trusted services

The plan anticipates later migration of artifacts to S3 or a storage abstraction, but current code is filesystem-backed only.

## See Also

- [03 Code Map](03_code_map.md)
- [06 Interfaces](06_interfaces.md)
- [08 Security](08_security.md)

