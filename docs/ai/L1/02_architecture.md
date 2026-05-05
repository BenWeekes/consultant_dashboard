# 02 Architecture

> Explain what this service owns, what it does not own, and how data flows through it.

## Role in the System

This repo is the product/admin service in a larger multi-service setup.

It owns:

- consultant/admin authentication
- client records and consultant-to-client assignment
- session index and therapist/consultant-facing review pages
- audit log storage
- encrypted storage references for summaries, biomarker aggregates, and alerts
- client messaging threads and secure reply links
- meeting scheduling, hosted meeting response pages, and meeting review pages
- internal APIs for:
  - client resolution
  - start-of-session context fetch
  - meeting join authorization
  - end-of-call ingestion
  - AI-human crisis escalation init/status

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
simple-backend ----signed POST---> consultant-dashboard /internal/authorize-meeting-join
server-custom-llm -signed POST---> consultant-dashboard /internal/session-complete
server-custom-llm -signed POST---> consultant-dashboard /internal/crisis-escalate-init
server-custom-llm -signed POST---> consultant-dashboard /internal/crisis-escalate-status

consultant/admin browser ---> consultant-dashboard web routes
client secure reply link -> consultant-dashboard /client/messages/<token>
client meeting link -----> consultant-dashboard /meetings/respond/<token>
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
5. Service returns current notes, direction, demographics, recent summary, baseline, open alerts, and the stored client key point summaries.
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
- `ai_personal_summary` provides the current `Client Key Point Summary - AI Sessions`
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
4. Service stores the LLM-provided client key point summary for the relevant session type:
   - AI-human session -> `ai_summary_storage_key`
   - human-human session -> `human_summary_storage_key`
5. Service recomputes the rolling biomarker baseline from the latest ten stored biomarker snapshots.
6. Service refreshes AI/human session counts, stores session alerts, and writes an audit log row.
7. If crisis escalation occurred, the linked `escalation_events` row shares the same `session_id`.

Client feedback path:

- when the client ends an AI or human session, the React client posts `rating`, optional `comment`, and `avatar_id` to `POST /v/<vendor>/session-feedback`
- if the session row already exists, feedback is written directly to `session_feedback`
- if the session row does not exist yet, feedback is staged in `pending_session_feedback`
- during `/internal/session-complete`, matching pending feedback is claimed and merged onto the final session row
- staged feedback is only attached when the pending row `client_id` matches the completed session `client_id`

Additional current behavior:

- per-session biomarker artifacts also persist:
  - `history_averages`
  - `history_window_sessions`
- those values represent the prior-session biomarker baseline as it existed when that session ended, not the client baseline today
- `urgent_escalation` is inferred from explicit payloads, crisis-level safety stats, or linked escalation events if the sender omits it

The dashboard summary is intended to stay consultant-facing and generalized. Richer runtime continuity memory remains the responsibility of `server-custom-llm` and should not be treated as the same artifact.

Current end-of-call split:

- continuity memory: private AI follow-up summary stored by `server-custom-llm`
- session summary: generalized dashboard summary with:
  - `key_point_summary`
  - `brief_overview`
  - `full_summary`
  - `biomarker_summary`
  - `risk_overview`
  - `follow_up`
- client key point summary: updated long-lived AI or human summary generated by `server-custom-llm` and stored by the dashboard

### AI-human crisis escalation

1. `server-custom-llm` receives a Thymia safety result at or above the crisis threshold.
2. It calls signed `POST /internal/crisis-escalate-init`.
3. This service validates `client_id` + `session_id`, optionally validates `meeting_id`, and creates or reuses an `escalation_events` row.
4. `server-custom-llm` dials the PSTN leg into the same Agora channel.
5. It posts signed `POST /internal/crisis-escalate-status` transitions (`dialing`, `answered`, `failed`, `completed`).

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

### Consultant live meetings

1. Consultant schedules a meeting from the client detail page.
2. Service creates:
   - a `scheduled_meetings` row
   - the associated `client_access_links` row used by the hosted response page
   - an invite email with ICS when delivery is configured
3. Client opens `/meetings/respond/<token>` and accepts or declines.
4. Consultant join goes through a short-lived signed bootstrap URL back into the React client.
5. Client join goes through the hosted response page token.
6. `simple-backend` sends signed `POST /internal/authorize-meeting-join`.
7. This service authorizes join based on current meeting state, time window, participant role, and ownership.
8. `simple-backend` mints RTC/RTM tokens only after authorization succeeds.
9. `server-custom-llm` runs in generic `meeting_mode` and posts deterministic completion artifacts back through `/internal/session-complete`.

Phase-1 meeting behavior:

- consultant and client both see the client's biomarkers
- consultant biomarkers are not captured
- meetings remain first-class records and also create linked `sessions` rows on completion
- future meetings only clash when their scheduled times overlap; multiple weekly/monthly meetings for the same client are allowed when times do not overlap

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

Recurring meeting notes:

- `scheduled_meetings.repeat_frequency` is the canonical recurrence field
- current values are:
  - `none`
  - `weekly`
  - `monthly`
- legacy `repeat_weekly` still exists for backward compatibility but should not be treated as the primary field in new code

## Current Boundaries

Current implementation assumes:

- one service-local SQLite database
- local/shared filesystem storage
- HMAC-signed internal HTTP between trusted services
- AI-human escalation can exist without a scheduled meeting row

The plan anticipates later migration of artifacts to S3 or a storage abstraction, but current code is filesystem-backed only.

## Related Deep Dives

- [Therapy Stack Setup](L2/therapy_stack_setup.md) — operational view of the full local stack
