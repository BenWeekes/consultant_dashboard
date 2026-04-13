# 03 Code Map

Purpose: quickly answer “where does this behavior live?”

## Top-Level Layout

```text
consultant-dashboard/
├── AGENTS.md
├── CLAUDE.md
├── docs/ai/
├── config/
│   └── admin_auth.conf.example
├── consultant_dashboard/
│   ├── app.py
│   ├── core/
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── internal_api.py
│   │   ├── schema.sql
│   │   ├── storage.py
│   │   └── web.py
│   └── templates/
├── requirements.txt
├── run.py
└── tests/
    ├── support.py
    ├── test_internal_api.py
    ├── test_smoke.py
    └── test_web_dashboard.py
```

## Entry Points

- `run.py`
  - CLI launcher
  - delegates to `consultant_dashboard.app:main`
- `consultant_dashboard/app.py`
  - app factory
  - config loading
  - blueprint registration
  - CLI subcommands

## Core Modules

### `consultant_dashboard/core/config.py`

Use for:

- env var loading
- path normalization
- parsing admin auth file for session config

### `consultant_dashboard/core/auth.py`

Use for:

- consultant login flow
- consultant OTP verification
- admin login flow
- role guards (`require_consultant`, `require_admin`)
- admin auth file validation

### `consultant_dashboard/core/db.py`

Use for:

- schema initialization
- SQLite connection helper
- consultant/client/session CRUD helpers
- audit logging helpers
- client resolution and context queries

If a change is “business data in SQLite,” it probably lands here.

### `consultant_dashboard/core/internal_api.py`

Use for:

- signed service-to-service APIs
- client resolution
- client context reads
- end-of-call session ingestion
- baseline computation during ingestion

### `consultant_dashboard/core/storage.py`

Use for:

- encrypted artifact persistence
- per-client HKDF-derived data encryption keys
- JSON read/write for encrypted blobs

### `consultant_dashboard/core/web.py`

Use for:

- consultant/admin dashboards
- client list/detail pages
- session list/detail pages
- admin consultant management

## Templates

Shared layout:

- `templates/shared/base.html`
- `templates/shared/home.html`

Consultant views:

- `templates/consultant/login.html`
- `templates/consultant/verify.html`
- `templates/consultant/dashboard.html`
- `templates/consultant/clients.html`
- `templates/consultant/client_detail.html`
- `templates/consultant/sessions.html`
- `templates/consultant/session_detail.html`

Admin views:

- `templates/admin/login.html`
- `templates/admin/dashboard.html`
- `templates/admin/consultants.html`

## Tests

- `tests/test_smoke.py`
- `tests/test_internal_api.py`
- `tests/test_web_dashboard.py`
- `tests/support.py`

Best first stops:

- broad sanity check: `tests/test_smoke.py`
- internal signing/ingestion work: `tests/test_internal_api.py`
- login and dashboard flow work: `tests/test_web_dashboard.py`

## Where To Edit

- Add a new internal endpoint:
  - `core/internal_api.py`
  - maybe `core/db.py`
  - update `docs/ai/L1/06_interfaces.md`
- Add a new dashboard page:
  - `core/web.py`
  - matching template
- Add a new DB table or field:
  - `core/schema.sql`
  - `core/db.py`
  - maybe ingestion/read paths
- Change encryption/storage behavior:
  - `core/storage.py`
  - callers in `core/internal_api.py` and `core/web.py`

## See Also

- [04 Conventions](04_conventions.md)
- [05 Workflows](05_workflows.md)
