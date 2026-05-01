# 08 Security

> Explain trust boundaries, secret handling, and how sensitive therapy and consultation data is protected.

## Trust Boundaries

There are three main classes of caller:

- browser users
  - consultant
  - admin
- trusted internal services
  - `simple-backend`
  - `server-custom-llm`
- local operators running CLI commands
- hosted client meeting/message token holders

Each class uses a different mechanism.

## Browser Auth

Consultants:

- authenticate with email/password from the `consultants` table
- then complete OTP verification
- session is stored in Flask’s session cookie
- production TTL is configured to 1 hour
- a localhost-only support route can also create a consultant session, but only when explicitly enabled and only from the dashboard host itself

Admins:

- authenticate from the file-based admin auth file
- session is stored in Flask’s session cookie

Current state:

- consultant flow has 2FA
- admin flow is password-only today
- clients can also reach hosted secure pages through hashed `client_access_links`
- client-authenticated browser access is carried by a shared 1-hour auth cookie after successful backend login + OTP
- AI-human crisis escalation events are internal-service initiated only; no browser route can start them directly

Local support-login hard requirements:

- `CONSULTANT_LOCAL_SUPPORT_LOGIN_ENABLED=true`
- `CONSULTANT_LOCAL_SUPPORT_LOGIN_SECRET` set to a strong value
- request originates from `127.0.0.1` or `::1`
- operator logs in as a real consultant email on the current vendor
- success and failure are both audit logged

## Internal Service Auth

Protected `/internal/*` routes require HMAC authentication:

- `X-Consultant-Timestamp`
- `X-Consultant-Signature`

Protections:

- request signing with shared secret
- 5-minute timestamp freshness window
- exact path + method + payload binding in the signature
- crisis init/status uses the same signed internal contract

Meeting-specific trust rule:

- the hosted client meeting token is the room locator and meeting-context anchor for guest join
- but the token never grants media access by itself
- guest room entry must also present a valid `simple-backend` auth session that resolves to the same dashboard client as the meeting
- `simple-backend` must call signed `POST /internal/authorize-meeting-join` and mint RTC/RTM only after the dashboard confirms:
  - current meeting state
  - join window
  - cancellation / decline status
  - participant role

## Encryption at Rest

Sensitive JSON artifacts are encrypted before writing to disk.

Implementation:

- master key source: `THERAPY_MASTER_KEY`
- per-write random salt
- per-client HKDF-derived 32-byte key
- AES-GCM for authenticated encryption

This means filesystem reads alone should not reveal session history.

Hosted token handling:

- secure message links and meeting response links both reuse `client_access_links`
- only token hashes are stored in SQLite
- hosted realtime and hosted HTTP routes both enforce token expiry
- consultant join uses a short-lived signed bootstrap URL rather than trusting raw browser parameters
- hosted client pages still require a valid client auth cookie; the bearer link only identifies the target resource

## What Stays in SQLite vs Encrypted Artifacts

SQLite stores:

- relational metadata
- IDs
- assignments
- audit rows
- escalation event rows and call-state metadata
- storage key references

Encrypted artifacts store:

- session summaries
- biomarker aggregates
- alert detail payloads
- rolling biomarker baselines

Current tradeoff:

- some sensitive-but-operational fields such as `notes_current` and `direction_current` are still inline in SQLite
- if their sensitivity level increases, they should move to encrypted artifacts or field-level encryption

## Secret Handling

Secrets must not be committed:

- `.env`
- `config/admin_auth.conf`
- runtime data under `data/`
- SIP-CM auth tokens
- Agora app certificates

Tracked files expose only examples:

- `.env.example`
- `config/admin_auth.conf.example`

## Logging and Audit

Audit rows are stored in `audit_log`.

Typical captured data:

- actor type/id
- action
- target type/id
- session id
- request metadata
- JSON details

Be careful not to add raw personal or clinical detail into audit payloads unless absolutely necessary.

## Known Gaps

- no CSRF protection layer exists yet for write routes
- no rate limiting or brute-force throttling exists yet
- admin login does not yet use OTP
- there is no key rotation mechanism yet for `THERAPY_MASTER_KEY`
- there is no outbox/retry queue yet for failed external event delivery because this repo currently only receives events
- session deletion is still a hard delete; soft-delete plus preserved audit semantics is still pending
- hosted meeting and message pages still begin from bearer-style URLs, so email interception remains part of the attack surface even though guest room entry is separately gated by backend auth

## Roadmap Security / Integrity Items

These are not implemented yet, but they are the current intended hardening direction:

- add CSRF protection for consultant/admin write routes
- change session deletion to soft-delete instead of hard delete
- add admin OTP / second factor
- add rate limiting and brute-force protection
- add key rotation support for encrypted artifacts
- add a delivery outbox / retry model for outbound notifications and internal event forwarding

## Security-Sensitive Changes

When changing any of these, update this file:

- auth flows
- session cookie behavior
- internal request signing
- encryption/storage format
- audit payload shape

## Related Deep Dives

- None
