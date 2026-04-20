# 08 Security

Purpose: explain trust boundaries, secret handling, and how sensitive therapy/consultation data is protected.

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

Admins:

- authenticate from the file-based admin auth file
- session is stored in Flask’s session cookie

Current state:

- consultant flow has 2FA
- admin flow is password-only today
- clients can also reach hosted secure pages through hashed `client_access_links`

## Internal Service Auth

Protected `/internal/*` routes require HMAC authentication:

- `X-Consultant-Timestamp`
- `X-Consultant-Signature`

Protections:

- request signing with shared secret
- 5-minute timestamp freshness window
- exact path + method + payload binding in the signature

Meeting-specific trust rule:

- the hosted client meeting token is the trust anchor for guest join
- but the token never grants media access by itself
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

## What Stays in SQLite vs Encrypted Artifacts

SQLite stores:

- relational metadata
- IDs
- assignments
- audit rows
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
- hosted links are bearer-style URLs, so email interception remains the main practical attack surface until a stronger second factor is added to that flow

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

## See Also

- [02 Architecture](02_architecture.md)
- [06 Interfaces](06_interfaces.md)
- [07 Gotchas](07_gotchas.md)
