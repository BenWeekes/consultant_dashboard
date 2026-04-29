# 04 Conventions

> Capture repo-specific implementation patterns that new changes should match.

## App Structure

- keep request-facing behavior inside Flask blueprints
- keep low-level persistence helpers in `core/db.py`
- keep config parsing in `core/config.py`
- keep encryption logic in `core/storage.py`
- avoid scattering direct SQLite queries into templates or unrelated modules

## Config Conventions

- required settings are loaded via `_require_env`
- paths are normalized to absolute paths early
- admin auth file validation happens during app startup and CLI execution
- local secrets belong in `.env` or `config/admin_auth.conf`, both ignored by git

## Database Conventions

- `get_db()` always enables foreign keys
- schema is managed through `schema.sql`
- helper functions should accept an open `sqlite3.Connection`
- call sites own `commit()` / `rollback()` / `close()`
- use rows as dictionaries via `sqlite3.Row`

## Web Route Conventions

- consultant-facing routes live under `/consultant/...`
- admin-facing routes live under `/admin/...`
- shared routes stay under `/` or `/home`
- role enforcement uses decorators from `auth.py`
- unauthorized users are redirected to the relevant login page

## Internal API Conventions

- all non-health internal endpoints require HMAC headers
- canonical signature input is:

```text
{timestamp}.{method}.{path}.{payload}
```

- GET payload = raw query string
- POST payload = raw request body
- internal endpoint responses are JSON

## Storage Conventions

- structured metadata stays in SQLite
- sensitive summaries/biomarkers/alert details stay encrypted on disk
- storage keys are namespaced by `client_id` and `session_id`
- callers do not manage raw AES keys directly; they use `EncryptedStorage`

## Testing Conventions

- smoke coverage lives in `tests/test_smoke.py`
- use temp directories in tests for DB, storage root, and admin auth file
- test signed internal endpoints through the Flask test client where possible

## Documentation Conventions

- keep `docs/ai/L0_repo_card.md` `Last Reviewed` current when `docs/ai/` changes
- update:
  - `03_code_map.md` for structural/file changes
  - `05_workflows.md` for process changes
  - `06_interfaces.md` for contract changes
  - `07_gotchas.md` when a new trap is discovered

## Related Deep Dives

- None
