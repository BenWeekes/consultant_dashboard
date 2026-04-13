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
  --phone +447700900111
python run.py link-client-auth \
  --client-id <client-id> \
  --email alex@example.com \
  --name "Alex Demo" \
  --phone +447700900111
python run.py serve
```

Open:

- `http://127.0.0.1:8090/health`
- `http://127.0.0.1:8090/consultant/login`
- `http://127.0.0.1:8090/admin/login`

## Internal APIs

- `GET /internal/health`
- `GET /internal/resolve-client`
- `GET /internal/client-context`
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

## Tests

Test modules live under `tests/`:

- `tests/test_smoke.py`
- `tests/test_internal_api.py`
- `tests/test_web_dashboard.py`

Run the full suite:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

Coverage currently includes:

- consultant/admin login success and failure cases
- route protection redirects
- internal API signing failures
- full `resolve-client`, `client-context`, and `session-complete` flows
- consultant client/session pages
- admin consultant creation and duplicate handling

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
