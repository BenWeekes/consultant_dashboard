# 01 Setup

> Boot the dashboard locally, understand the required secrets, and know which deeper runbooks to use.

This repo owns the product and admin layer: consultants, clients, meetings, reminders, and encrypted session artifacts. Client RTC boot, Agora transports, Shen, and Thymia live primarily in the therapy-profile sample stack outside this repo.

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

Core URLs:

- `http://127.0.0.1:8090/health`
- `http://127.0.0.1:8090/consultant/login`
- `http://127.0.0.1:8090/admin/login`
- `http://127.0.0.1:8090/internal/health`

## Required Environment

Set in `.env`:

- `CONSULTANT_DB_PATH`
- `THERAPY_STORAGE_ROOT`
- `THERAPY_MASTER_KEY`
- `CONSULTANT_INTERNAL_SHARED_SECRET`
- `CONSULTANT_ADMIN_AUTH_FILE`

Common optional config:

- `CONSULTANT_PUBLIC_BASE_URL`
- `CONSULTANT_SESSION_TTL`
- `CONSULTANT_AUTH_DEV_MODE`
- `THERAPY_DASHBOARD_BRAND_NAME`
- `CONSULTANT_SENDGRID_API_KEY`
- `CONSULTANT_EMAIL_FROM`
- `CONSULTANT_TWILIO_ACCOUNT_SID`
- `CONSULTANT_TWILIO_AUTH_TOKEN`
- `THERAPY_CLIENT_APP_URL`

## Admin Auth File

File: `config/admin_auth.conf`

Requirements:

- must exist before app start
- must not be a directory
- must be mode `600` or stricter
- must contain `session_secret`
- must contain at least one `email=password_hash` entry

Generate hashes with:

```bash
python run.py hash-password --password 'changeme123'
```

## Day-One Commands

Create consultant:

```bash
python run.py create-consultant --email ... --name ... --phone ... --password ...
```

Create client:

```bash
python run.py create-client --consultant-id ... --name ... --email ... --phone ...
```

Run reminders locally:

```bash
source venv/bin/activate
python scripts/run_reminders.py
```

Full tests:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

## What This Repo Does In The Meeting Flow

- consultants schedule meetings and send secure links
- client response links are hosted here
- `simple-backend` calls signed dashboard APIs to authorize meeting joins
- `server-custom-llm` posts completed session artifacts back here
- reminder delivery is driven through the internal reminder sweep

## When To Use L2

Use the deep dives when you need more than the local boot path:

- full cross-repo bring-up, tunnel setup, and restart order
- reminder cron details and live-stack checks
- Playwright session-cookie minting
- exact local/public URL combinations

## Related Deep Dives

- [therapy_stack_setup.md](L2/therapy_stack_setup.md)
- [operational_runbook.md](L2/operational_runbook.md)
