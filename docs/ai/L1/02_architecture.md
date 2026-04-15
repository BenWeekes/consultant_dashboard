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
- client messaging threads and secure reply links
- internal APIs for:
  - client resolution
  - start-of-session context fetch
  - end-of-call ingestion

It does not own:

- Agora session launch
- live call orchestration
- live memory generation during the call
- client-facing Google/SMS login screens

Those stay in sibling services such as `simple-backend` and `server-custom-llm`.

## High-Level Component Model

```text
simple-backend ----signed GET----> consultant-dashboard /internal/resolve-client
simple-backend ----signed GET----> consultant-dashboard /internal/client-context
server-custom-llm -signed POST---> consultant-dashboard /internal/session-complete

consultant/admin browser ---> consultant-dashboard web routes
client secure reply link -> consultant-dashboard /client/messages/<token>
client browser -----------> simple-backend auth + react client

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
5. Admins can replace a consultant password from the consultant detail page by setting a new temporary password.

### Admin login

1. Admin submits email/password.
2. Credentials are checked against `config/admin_auth.conf`.
3. Session is created directly; no separate OTP exists yet for admins.

### Start-of-session support

1. Client authenticates in `simple-backend` using either email/password + phone verification, or Google + phone verification.
2. `simple-backend` hashes the authenticated identity fields and sends signed `GET /internal/resolve-client`.
3. Service maps those hashes to `client_id` in `client_auth_identities`.
4. `simple-backend` sends signed `GET /internal/client-context?client_id=...`.
5. Service returns current notes, direction, recent summary, baseline, and open alerts.
6. `simple-backend` passes the resulting context and dashboard callback config into `server-custom-llm`.

Normal production expectation:

- email hash + phone hash are the primary client match keys
- Google identity can still be sent as supporting identity data
- client password verification happens through signed `POST /internal/verify-client-password`
- if the session-launch profile enables required dashboard authorization, `simple-backend` should deny access when no dashboard client record matches
- only US and UK phone numbers are accepted in the dashboard and in the client auth form for now
- clients can use dashboard-managed email/password + SMS, and Google + SMS remains optional for matching email accounts

This is how the consultant influences the next AI session:

- `notes` provide durable background context for the AI
- `direction` provides explicit steering for the next session
- `latest_summary` gives the AI a generalized view of the last session
- `baseline` gives the AI the client biomarker reference point

Ownership rule:

- each client belongs to one consultant at a time
- the DB enforces one `consultant_clients` row per `client_id`
- reassignment should be treated as changing the owner, not as concurrent multi-consultant access

### End-of-call ingestion

1. `server-custom-llm` sends signed `POST /internal/session-complete`.
2. Service encrypts and stores summary/biomarker/alert payloads under `THERAPY_STORAGE_ROOT`.
3. Service upserts the `sessions` row.
4. Service recomputes the rolling biomarker baseline from the latest five stored biomarker snapshots.
5. Service stores session alerts and writes an audit log row.

The dashboard summary is intended to stay consultant-facing and generalized. Richer runtime continuity memory remains the responsibility of `server-custom-llm` and should not be treated as the same artifact.

Current end-of-call split:

- continuity memory: private AI follow-up summary stored by `server-custom-llm`
- consultant summary: generalized dashboard summary with overview, biomarker summary, risk overview, and follow-up guidance

### Consultant messaging

1. Consultant composes a message from the client record.
2. Service creates a secure access token in `client_access_links`.
3. Service stores the outbound record in `client_messages`.
4. If configured:
   - email is delivered through SendGrid when the client has an email address
   - SMS is delivered through Twilio Messaging when the client has no email address but does have a phone number
5. The outbound notification includes a secure reply URL hosted by this service.
6. Client replies through the hosted UI instead of inbound SMS, and the reply is stored in `client_messages` as an inbound `portal` message.
7. Consultant and client pages subscribe to realtime thread updates over WebSocket.

Important current behavior:

- secure reply links are only stored hashed in `client_access_links`
- outbound message metadata does not store the raw reply URL
- the client WebSocket path now enforces the same access-link expiry rule as the HTTP reply page
- the current chat UI auto-refreshes on thread updates, but notification delivery is still immediate rather than presence-aware

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
