# 01 Setup

Purpose: boot the service locally, seed initial records, and understand the required runtime configuration.

This repo documents the product/admin layer. Client session boot, Agora channel setup, Shen, and Thymia live primarily in the sample-stack recipe at `agent-samples/recipes/therapist.md`.

For the full cross-repo local runbook, including tunnel setup and restart order, use:

- [Therapy Stack Setup](deep_dives/therapy_stack_setup.md)

## Stack

- Python service: Flask app in `consultant_dashboard/`
- DB: SQLite file at `CONSULTANT_DB_PATH`
- Encrypted artifacts: filesystem root at `THERAPY_STORAGE_ROOT`
- Auth:
  - consultant login = email/password + OTP
  - admin login = file-based email/password
  - clients do not use normal dashboard logins; they authenticate through `simple-backend`, and can also open secure message links hosted here
- Tests:
  - `tests/test_smoke.py`
  - `tests/test_internal_api.py`
  - `tests/test_web_dashboard.py`
  - `tests/test_live_stack.py` (opt-in live service smoke test)
  - meeting lifecycle checks currently live in `tests/test_internal_api.py` and `tests/test_web_dashboard.py`

## Quick Start

```bash
cd consultant-dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/admin_auth.conf.example config/admin_auth.conf
chmod 600 config/admin_auth.conf
python run.py init-db
python run.py create-consultant \
  --email consultant@example.com \
  --name "Demo Consultant" \
  --phone +447700900000 \
  --password changeme
python run.py serve
```

Service URLs:

- `http://127.0.0.1:8090/health`
- `http://127.0.0.1:8090/consultant/login`
- `http://127.0.0.1:8090/admin/login`
- `http://127.0.0.1:8090/consultant/account`
- `http://127.0.0.1:8090/admin/account`
- `http://127.0.0.1:8090/internal/health`

## Required Environment Variables

Defined in `.env.example`:

- `CONSULTANT_DB_PATH`
- `THERAPY_STORAGE_ROOT`
- `THERAPY_MASTER_KEY`
- `CONSULTANT_INTERNAL_SHARED_SECRET`
- `CONSULTANT_ADMIN_AUTH_FILE`

Important optional config:

- `CONSULTANT_DASHBOARD_HOST`
- `CONSULTANT_DASHBOARD_PORT`
- `CONSULTANT_PUBLIC_BASE_URL`
- `CONSULTANT_SESSION_TTL`
- `CONSULTANT_AUTH_DEV_MODE`
- `THERAPY_DASHBOARD_BRAND_NAME`
- `CONSULTANT_TWILIO_ACCOUNT_SID`
- `CONSULTANT_TWILIO_AUTH_TOKEN`
- `CONSULTANT_TWILIO_VERIFY_SERVICE_SID`
- `CONSULTANT_TWILIO_MESSAGING_SERVICE_SID`
- `CONSULTANT_TWILIO_FROM_NUMBER`
- `CONSULTANT_SENDGRID_API_KEY`
- `CONSULTANT_EMAIL_FROM`
- `CONSULTANT_EMAIL_REPLY_TO`
- `CONSULTANT_OUTBOUND_REQUEST_TIMEOUT_SECONDS`
- `THERAPY_CLIENT_APP_URL`

Reminder runner:

- checked-in helper: `scripts/run_reminders.py`
- dashboard endpoint: `POST /internal/run-reminders`
- intended invocation: cron or another scheduler, not a request-path side effect

## Admin Auth File

File: `config/admin_auth.conf`

Rules:

- must exist before the app starts
- must not be a directory
- must be mode `600` or stricter
- must contain `session_secret`
- must contain at least one `email=password_hash` entry

Use:

```bash
python run.py hash-password --password 'changeme123'
```

## Seed and Helper Commands

Create consultant:

```bash
python run.py create-consultant --email ... --name ... --phone ... --password ...
```

Create client:

```bash
python run.py create-client --consultant-id ... --name ... --email ... --phone ...
```

Normal product flow does not require a CLI link step. When a consultant creates a client in the dashboard UI with name, email, and phone, the service creates the hashed identity rows that `simple-backend` later resolves after Google + SMS login.

Messaging flow:

- consultants send email/SMS/meeting-invite messages from the client detail page
- the system stores the message in `client_messages`
- outbound email uses SendGrid when configured
- outbound SMS uses Twilio Messaging when configured
- outbound messages include a secure link back to `/client/messages/<token>`
- client replies are captured through that hosted UI instead of inbound SMS

Meeting flow:

- consultants schedule meetings from `/consultant/clients/<client_id>/meetings/new`
- meeting creation also creates the `client_access_links` row used by the hosted response page
- clients accept or decline from `/meetings/respond/<token>`
- clients still need to authenticate through `simple-backend` before they can actually enter the room; the email link is not the final auth factor
- once authenticated, the client session is carried by a shared 1-hour auth cookie so invite pages and room entry do not trigger a second OTP inside that window
- consultant dashboard sessions are also configured for a 1-hour TTL in production so unattended devices do not stay logged in for long periods
- the durable user-facing entry point is the hosted meeting page, which mints a fresh short-lived room bootstrap when `Enter Meeting Room` is clicked
- `simple-backend` calls signed `POST /internal/authorize-meeting-join` before minting RTC/RTM tokens
- `server-custom-llm` posts deterministic meeting artifacts back through `POST /internal/session-complete`
- meeting reminders are sent by calling the signed internal reminder sweep endpoint through `scripts/run_reminders.py`

Run the reminder sweep locally:

```bash
source venv/bin/activate
python scripts/run_reminders.py
```

Example cron entry:

```cron
* * * * * cd /Users/benweekes/work/therapy/consultant-dashboard && ./venv/bin/python scripts/run_reminders.py --quiet >> /tmp/mindfix-reminders.log 2>&1
```

`link-client-auth` still exists as a low-level helper for tests, fixtures, or repair work.

## Verification

Run:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

Run the live stack smoke test:

```bash
source venv/bin/activate
RUN_LIVE_STACK_TESTS=1 python -m unittest tests.test_live_stack -v
```

Defaults:

- `LIVE_BASE_URL=https://mindfix.me`
- `LIVE_CUSTOM_LLM_URL=http://127.0.0.1:8101`
- `LIVE_TUNNEL_PING_URL=${LIVE_CUSTOM_LLM_URL}/ping`

Local Mac with one reverse-proxy domain/port:

```bash
RUN_LIVE_STACK_TESTS=1 LIVE_BASE_URL=http://localhost:8080 python -m unittest tests.test_live_stack -v
```

Local Mac with direct ports (no nginx):

```bash
RUN_LIVE_STACK_TESTS=1 \
LIVE_BACKEND_URL=http://127.0.0.1:8082 \
LIVE_DASHBOARD_URL=http://127.0.0.1:8090 \
LIVE_CLIENT_URL='http://localhost:8084?profile=therapy&autoconnect=true' \
LIVE_CUSTOM_LLM_URL=http://127.0.0.1:8101 \
python -m unittest tests.test_live_stack -v
```

Coverage includes:

- app boot
- consultant/admin login success and failure
- signed `GET /internal/resolve-client`
- signed `GET /internal/client-context`
- signed `POST /internal/authorize-meeting-join`
- signed `POST /internal/session-complete`
- dashboard route protections and render paths
- consultant/admin CRUD flows
- consultant/admin password change flows
- automatic client identity resolution contract used by `simple-backend`
- consultant meeting scheduling and response pages
- meeting completion linkage back onto `scheduled_meetings`

Live smoke coverage includes:

- `simple-backend` health
- `simple-backend` auth-check contract
- `consultant-dashboard` health
- `server-custom-llm` ping

## Playwright

Run the browser e2e checks:

```bash
cd /home/ubuntu/mindfix/consultant_dashboard/client
npm run test:e2e
```

Consultant upcoming meetings on the live site can be checked without a real password or OTP by minting a normal Flask session cookie locally on the server:

```bash
cd /home/ubuntu/mindfix/consultant_dashboard/client
PLAYWRIGHT_CONSULTANT_SESSION_COOKIE="$(
  ../venv/bin/python ../scripts/mint_consultant_session_cookie.py \
    --email benweekes73@gmail.com \
    --vendor-slug mindfix
)" npm run test:e2e -- tests/e2e/consultant-upcoming.spec.ts
```

If you want the Playwright check to assert a specific upcoming row, set:

```bash
PLAYWRIGHT_CONSULTANT_UPCOMING_EXPECTED="MindFix session"
```

Screenshots are written under:

- `client/test-results/`

## Browser E2E Checks

The React client now has Playwright-based browser checks in:

- `client/tests/e2e/app-smoke.spec.ts`
- `client/tests/e2e/ai-biomarkers.spec.ts`

These are useful when a normal browser refresh is not enough to prove:

- the app is serving current JS chunks
- `/app` does not crash during hydration
- an authenticated session can actually render the `Biomarkers` tab
- the layout looks sane on desktop and mobile screenshots

Install once on the server or dev machine:

```bash
cd client
npm install
npx playwright install chromium
sudo npx playwright install-deps chromium
```

Run the unauthenticated smoke checks:

```bash
cd client
npm run test:e2e
```

Current smoke coverage:

- public home loads without broken JS/CSS
- `/app` loads without stale chunk failures or page-level runtime errors before auth

Run the authenticated biomarker checks:

```bash
cd client
PLAYWRIGHT_CLIENT_AUTH_COOKIE='<mindfix_client_auth cookie>' \
npx playwright test tests/e2e/ai-biomarkers.spec.ts
```

Authenticated biomarker coverage:

- opens a real authenticated AI session
- clicks the `Biomarkers` tab
- verifies the tab content renders
- saves screenshots for desktop and mobile layouts
- lets you visually review the actual in-session layout instead of guessing from code alone

Screenshot output:

- `client/test-results/ai-biomarkers.png`
- `client/test-results/ai-biomarkers-mobile.png`

These screenshots are debugging artifacts, not product assets. Do not commit `client/test-results/`.

### Getting An Auth Cookie

Simplest path:

1. log in through the real client auth flow in a browser
2. copy the `mindfix_client_auth` cookie value
3. export it as `PLAYWRIGHT_CLIENT_AUTH_COOKIE`

For server-side debugging against the live stack, a valid cookie can also be minted from the backend JWT secret if you already know the target `client_id`. Example shape:

```python
import hashlib, time, jwt

client_id = "..."
user_id = hashlib.sha256(f"client|{client_id}".encode()).hexdigest()
now = int(time.time())
token = jwt.encode(
    {
        "user_id": user_id,
        "client_id": client_id,
        "email": "client@example.com",
        "name": "Client Name",
        "first_name": "Client",
        "vendor_slug": "mindfix",
        "iat": now,
        "exp": now + 3600,
    },
    AUTH_JWT_SECRET,
    algorithm="HS256",
)
print(token)
```

Use this only for controlled debugging on trusted infrastructure.
- configurable `LIVE_TUNNEL_PING_URL` (defaults to `${LIVE_CUSTOM_LLM_URL}/ping`)

## Local Restart Discipline

When local behavior does not match the code on disk, assume a stale bound process first.

Check active listeners:

```bash
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:8082 -sTCP:LISTEN
```

Kill the exact stale PID and restart the intended service, then verify:

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8082/health
```

Typical symptoms:

- dashboard templates look unchanged after edits
- `/join-meeting` or other backend behavior looks impossible for the current code
- a new tunnel URL is configured on disk but Agora still appears to use an older one

Required discipline before telling the user a local fix is live:

1. edit the code
2. identify the bound PID with `lsof`
3. kill that exact PID
4. restart the intended service
5. verify the matching `/health` endpoint
6. reload the actual rendered page or rerun the exact request path

Do not trust template files or a normal browser refresh by themselves.

## Public Website (www/mindfix)

Static marketing site at `www/mindfix/`. Files are prod-ready in git — all links point to production URLs (`https://app.mindfix.me`, `https://dashboard.mindfix.me`).

### Local Development

When opened from `localhost` or `127.0.0.1`, URLs are automatically rewritten to local ports (`http://localhost:8084` for the client, `http://127.0.0.1:8090` for the dashboard).

To override manually, use query params:

```
www/mindfix/index.html?client_base=http://localhost:8084&dashboard_base=http://127.0.0.1:8090
```

No build step needed.

### Structure

```
www/mindfix/
  index.html      — single-page site (all sections)
  privacy.html    — privacy policy
  terms.html      — terms of service
  css/style.css   — custom styles (Bootstrap 5 + teal palette)
  img/            — avatar placeholders (replace with real images)
```

### Production Deployment

Serve `www/mindfix/` from the same domain as the dashboard. Links use relative paths (`/app`, `/consultant/login`, `/admin/login`) so no sed, no build, no env vars needed.

## See Also

- [02 Architecture](02_architecture.md)
- [06 Interfaces](06_interfaces.md)
- [07 Gotchas](07_gotchas.md)
