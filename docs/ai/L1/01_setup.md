# 01 Setup

Purpose: boot the service locally, seed initial records, and understand the required runtime configuration.

## Stack

- Python service: Flask app in `consultant_dashboard/`
- DB: SQLite file at `CONSULTANT_DB_PATH`
- Encrypted artifacts: filesystem root at `THERAPY_STORAGE_ROOT`
- Auth:
  - consultant login = email/password + OTP
  - admin login = file-based email/password
- Tests:
  - `tests/test_smoke.py`
  - `tests/test_internal_api.py`
  - `tests/test_web_dashboard.py`

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
- `CONSULTANT_SESSION_TTL`
- `CONSULTANT_AUTH_DEV_MODE`
- `THERAPY_DASHBOARD_BRAND_NAME`
- `CONSULTANT_TWILIO_ACCOUNT_SID`
- `CONSULTANT_TWILIO_AUTH_TOKEN`
- `CONSULTANT_TWILIO_VERIFY_SERVICE_SID`

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

Link hashed auth identity for `simple-backend` lookup testing:

```bash
python run.py link-client-auth --client-id ... --email ... --name ... --phone ...
```

## Verification

Run:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

Coverage includes:

- app boot
- consultant/admin login success and failure
- signed `GET /internal/resolve-client`
- signed `GET /internal/client-context`
- signed `POST /internal/session-complete`
- dashboard route protections and render paths
- consultant/admin CRUD flows

## See Also

- [02 Architecture](02_architecture.md)
- [06 Interfaces](06_interfaces.md)
- [07 Gotchas](07_gotchas.md)
