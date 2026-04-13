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

## Internal Service Auth

Protected `/internal/*` routes require HMAC authentication:

- `X-Consultant-Timestamp`
- `X-Consultant-Signature`

Protections:

- request signing with shared secret
- 5-minute timestamp freshness window
- exact path + method + payload binding in the signature

## Encryption at Rest

Sensitive JSON artifacts are encrypted before writing to disk.

Implementation:

- master key source: `THERAPY_MASTER_KEY`
- per-write random salt
- per-client HKDF-derived 32-byte key
- AES-GCM for authenticated encryption

This means filesystem reads alone should not reveal session history.

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

