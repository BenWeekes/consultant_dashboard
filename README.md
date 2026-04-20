# Consultant Dashboard

Separate service for consultant/admin workflows, client records, session review, biomarker baselines, and internal APIs used by `simple-backend` and `server-custom-llm`.

## Scope

This service owns:

- consultant/admin auth
- SQLite metadata
- encrypted artifact references
- client resolution APIs
- start-of-session context APIs
- end-of-call ingestion APIs
- consultant-to-client messaging with secure reply links
- consultant live meeting scheduling, response pages, and review pages

It does **not** start Agora sessions directly. `simple-backend` stays responsible for that path.

## Quick Start

```bash
cd consultant-dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/admin_auth.conf.example config/admin_auth.conf
python run.py init-db
python run.py create-consultant \
  --email consultant@example.com \
  --name "Demo Consultant" \
  --phone +447700900000 \
  --password changeme
python run.py create-client \
  --consultant-id <consultant-id> \
  --name "Alex Demo" \
  --email alex@example.com \
  --password clientpass123 \
  --phone +447700900111
python run.py serve
```

Open:

- `http://127.0.0.1:8090/health`
- `http://127.0.0.1:8090/consultant/login`
- `http://127.0.0.1:8090/admin/login`
- `http://127.0.0.1:8090/consultant/account`
- `http://127.0.0.1:8090/admin/account`

Normal dashboard behavior:

- consultants and admins have different login URLs
- clients do not use normal dashboard logins, but they can open secure message links hosted by this service
- client access is controlled by the client record email + phone number, with optional client password support
- each client belongs to one consultant at a time
- only US and UK phone numbers are supported right now
- admins can set or reset consultant passwords from the consultant detail page
- consultants manage client access by updating the client email, phone, and optional password fields
- consultants can schedule live meetings from the client detail flow
- clients accept or decline meetings from hosted response pages on this service
- consultant and client join the live meeting through the React client after dashboard-backed authorization
- client replies are expected through secure web links, not inbound SMS

Messaging delivery config:

- SendGrid email:
  - `CONSULTANT_SENDGRID_API_KEY`
  - `CONSULTANT_EMAIL_FROM`
  - optional `CONSULTANT_EMAIL_REPLY_TO`
- Twilio outbound messaging:
  - `CONSULTANT_TWILIO_ACCOUNT_SID`
  - `CONSULTANT_TWILIO_AUTH_TOKEN`
  - `CONSULTANT_TWILIO_MESSAGING_SERVICE_SID` or `CONSULTANT_TWILIO_FROM_NUMBER`
- secure link host:
  - `CONSULTANT_PUBLIC_BASE_URL`

## Internal APIs

- `GET /internal/health`
- `GET /internal/resolve-client`
- `GET /internal/client-context`
- `POST /internal/authorize-meeting-join`
- `POST /internal/run-reminders`
- `POST /internal/session-complete`

Internal authenticated requests require:

- `X-Consultant-Timestamp`
- `X-Consultant-Signature`

Signature format:

```text
hex(hmac_sha256(CONSULTANT_INTERNAL_SHARED_SECRET, "{timestamp}.{method}.{path}.{payload}"))
```

For `GET`, `payload` is the raw query string.
For `POST`, `payload` is the raw request body.

## CLI Commands

- `python run.py hash-password --password <value>`
- `python run.py init-db`
- `python run.py create-consultant ...`
- `python run.py create-client ...`
- `python run.py link-client-auth ...`

## Reminder Cron

Meeting reminders are triggered by the dashboard itself through a signed internal endpoint:

- `POST /internal/run-reminders`

A checked-in helper script calls that endpoint with the required HMAC headers:

```bash
cd consultant-dashboard
./venv/bin/python scripts/run_reminders.py
```

Example cron entry:

```cron
* * * * * cd /Users/benweekes/work/therapy/consultant-dashboard && ./venv/bin/python scripts/run_reminders.py --quiet >> /tmp/mindfix-reminders.log 2>&1
```

Current reminder behavior:

- sends a 24-hour reminder for future scheduled meetings
- sends a 1-minute reminder shortly before scheduled start
- skips immediate `meet now` meetings
- is idempotent via `reminder_24h_sent_at` / `reminder_1m_sent_at`

## Tests

Test modules live under `tests/`:

- `tests/test_smoke.py`
- `tests/test_internal_api.py`
- `tests/test_web_dashboard.py`
- `tests/test_live_stack.py` (opt-in live service smoke test)

Run the full suite:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

Coverage currently includes:

- consultant/admin login success and failure cases
- route protection redirects
- internal API signing failures
- full `resolve-client`, `verify-client-password`, `client-context`, and `session-complete` flows
- consultant client/session pages
- admin consultant creation, editing, and duplicate handling
- consultant/admin password change flows
- client password create/reset flows
- consultant outbound messaging and secure client replies
- consultant meeting scheduling and meeting response flows
- internal meeting-join authorization
- meeting completion linking from `session-complete` back onto `scheduled_meetings`

Optional live stack smoke test:

```bash
source venv/bin/activate
RUN_LIVE_STACK_TESTS=1 python -m unittest tests.test_live_stack -v
```

Default live endpoints checked:

- `http://127.0.0.1:8082/health`
- `http://127.0.0.1:8082/auth-check?...`
- `http://127.0.0.1:8090/health`
- `http://127.0.0.1:8101/ping`
- configured Cloudflare tunnel `/ping`

Override them with:

- `LIVE_BACKEND_URL`
- `LIVE_CLIENT_URL`
- `LIVE_DASHBOARD_URL`
- `LIVE_CUSTOM_LLM_URL`
- `LIVE_TUNNEL_PING_URL`

## Local Dev Note

If local behavior does not match the code you just changed, check for stale bound processes before debugging the feature itself.

Common ports:

- dashboard: `127.0.0.1:8090`
- simple-backend: `127.0.0.1:8082`

Useful checks:

```bash
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:8082 -sTCP:LISTEN
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8082/health
```

Recent local failures were caused by older processes still serving:

- old dashboard templates on `:8090`
- old `/join-meeting` behavior on `:8082`

Required local workflow after any dashboard template or backend behavior change:

1. find the exact listening PID with `lsof`
2. kill that PID
3. restart the service
4. verify `/health`
5. only then trust the browser/request result

Do not assume a save, hot reload, or browser refresh is enough.

## Admin Auth File

Set `CONSULTANT_ADMIN_AUTH_FILE` to a real file. The app refuses to start if it is missing or malformed.

Example format:

```ini
session_secret=replace-me
session_ttl=28800
admin@example.com=pbkdf2:sha256:1000000$...
```

Permissions should be:

```bash
chmod 600 config/admin_auth.conf
```
