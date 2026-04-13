# 07 Gotchas

Purpose: preserve the practical traps that are easy to rediscover the hard way.

## Current Gotchas

- **Python 3.9 compatibility matters in this environment.** The repo intentionally avoids `X | None` style annotations because the local interpreter is Python 3.9.
- **Werkzeug default password hashing was not portable here.** The repo uses `pbkdf2:sha256` explicitly because this environment did not expose `hashlib.scrypt`.
- **Consultant OTP logic has two modes.**
  - dev mode: locally generated/stored code, default value `000000`
  - production-style mode: Twilio Verify owns the code, so verification must use Twilio’s check endpoint instead of local comparison
- **`/internal/health` is the only unsigned internal endpoint.** New internal routes should not bypass signing unless they are intentionally public/health-only.
- **Admin auth file permissions are enforced at startup.** If `config/admin_auth.conf` is too open, the app refuses to start.
- **The repo currently uses direct schema initialization, not migrations.** Changing `schema.sql` is simple now, but production-safe schema evolution will need a migration story later.
- **Encrypted artifacts and SQLite must stay logically aligned.** A session row may point at storage keys; if callers change key formats without updating readers, dashboards break.
- **Current UI is server-rendered Flask templates.** Do not assume a JS app/router exists.

## Integration Caveats

- `simple-backend` integration is not wired yet in this repo.
- `server-custom-llm` event delivery is defined by contract here, but the external sender still needs to be updated separately.
- Artifact storage is filesystem-backed now even though the long-term direction is S3-compatible storage.

## Safe Assumptions

- internal callers are trusted services on a shared network, but still must sign requests
- consultant/client/session pages expect small-to-moderate result sets; pagination is not implemented yet
- baseline calculation is “latest 5 biomarker snapshots,” computed on ingestion

## Update Triggers

Update this file when:

- a production incident reveals a new failure mode
- an environment limitation changes required implementation style
- a new integration dependency adds setup or signing traps

## See Also

- [01 Setup](01_setup.md)
- [05 Workflows](05_workflows.md)
- [08 Security](08_security.md)

