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
- **Meeting-mode auth has two layers and both matter.**
  - the hosted invite/response link is only a room locator and meeting-context anchor
  - actual guest room entry must still pass through `simple-backend` auth and resolve to the same dashboard client as the meeting
  - if meeting-mode UI changes skip `/auth-check`, email-link possession turns back into an auth bypass
- **Cookie-based client auth still needs explicit fetch credentials in local split-port dev.**
  - production works on one origin behind nginx, so the shared 1-hour auth cookie is straightforward
  - local direct-port mode (`:8084` client calling `:8082` backend) is cross-origin, so frontend fetches must use `credentials: "include"` or auth appears to fail only on localhost
- **Playwright on a fresh server needs browser runtime packages, not just `npm install`.**
  - `@playwright/test` alone is not enough
  - Chromium launch will fail with missing shared libraries like `libatk-1.0.so.0` until you run:
    - `npx playwright install chromium`
    - `sudo npx playwright install-deps chromium`
- **Authenticated Playwright UI checks need a real client auth cookie.**
  - the `Biomarkers` screenshot test does not log in interactively
  - it expects `PLAYWRIGHT_CLIENT_AUTH_COOKIE` to contain a valid `mindfix_client_auth` cookie
  - if that cookie is expired, the browser test will bounce into auth and stop proving the in-session layout
- **Use fake media devices for browser automation or meeting/app tests will hang behind camera/mic prompts.**
  - Playwright launch args should include:
    - `--use-fake-ui-for-media-stream`
    - `--use-fake-device-for-media-stream`
    - `--autoplay-policy=no-user-gesture-required`
- **For the Next client, a successful build is not enough by itself.**
  - if `mindfix-client` is restarted before the build finishes, the live app can serve stale or missing chunk references and render a blank page
  - after changing the React client:
    1. finish `npm run build`
    2. restart `mindfix-client`
    3. verify the live page in a browser or Playwright before trusting the result
- **If local changes do not appear, you may still be hitting an old bound process.**
  - dashboard/template drift: check which PID is listening on `127.0.0.1:8090`, kill that specific process, and restart the dashboard cleanly
  - backend/join-flow drift: check which PID is listening on `127.0.0.1:8082`, kill that specific process, and restart `simple-backend` cleanly
  - do not trust a browser refresh alone when behavior looks impossible for the current code
  - verify the replacement process with `/health` before continuing
- **Do not tell the user a UI/template fix is live until you have restarted the exact bound PID and rechecked the rendered page.**
  - stale `:8090` has repeatedly served old templates and wasted review cycles
  - stale `:8082` has repeatedly served old join-flow behavior and produced misleading 500/403 errors
  - required discipline:
    1. change the code
    2. `lsof` the exact listening PID
    3. kill that PID
    4. restart the service cleanly
    5. verify `/health`
    6. only then claim the fix is live

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
