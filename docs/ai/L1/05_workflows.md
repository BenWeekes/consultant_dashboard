# 05 Workflows

> Provide fast paths for common maintenance and feature work.

## Add a New Internal Endpoint

1. Add the route in `core/internal_api.py`.
2. Reuse `_verify_internal_request()` and the shared HMAC pattern.
3. Put query/update helpers in `core/db.py` if logic touches SQLite.
4. If the endpoint reads/writes encrypted blobs, use `EncryptedStorage`.
5. Add or extend tests in `tests/test_internal_api.py`, and keep `tests/test_smoke.py` passing.
6. Update `06_interfaces.md`.

## Extend AI-human Crisis Escalation

1. Keep dashboard-owned escalation logic in:
   - `core/internal_api.py`
   - `core/db.py`
   - `core/agora_tokens.py`
2. Treat `meeting_id` as optional; AI-human escalation must still work with only:
   - `client_id`
   - `session_id`
   - `channel_name`
3. Keep `session_id` stable so `escalation_events` and final `sessions` rows can be correlated.
4. Add or extend tests in `tests/test_internal_api.py` for:
   - missing phone => `skipped`
   - non-numeric level => `400`
   - invalid phase => `400`
   - missing event => `404`
5. Update `06_interfaces.md` and `02_architecture.md`.
6. Re-run the targeted crisis slice:
   - `./venv/bin/python -m unittest tests.test_internal_api.CrisisEscalationInternalApiTest -v`

## Add a New Dashboard Page

1. Add the route in `core/web.py`.
2. Protect it with `require_consultant` or `require_admin` if needed.
3. Add a template under `templates/consultant/` or `templates/admin/`.
4. Fetch only the data the page needs from `core/db.py`.
5. Avoid putting business logic in Jinja templates.
6. Update `03_code_map.md` if the page adds a new stable area.

For biomarker-heavy consultant pages, keep the overview page compact:

- show only a few headline biomarker values by default
- use expandable grouped sections for the full metric set
- keep baseline comparison on the session detail page where there is more room

## Extend Client Messaging

1. Keep consultant-owned messaging in `core/web.py` and `core/messaging.py`.
2. Store outbound and inbound records in `client_messages` through `core/db.py`.
3. Reuse `client_access_links` for secure reply URLs instead of exposing consultant email addresses or phone numbers.
4. Keep a single send path for outbound consultant messages; avoid duplicating delivery + access-link creation logic across multiple routes.
5. Preserve the current delivery split:
   - email through SendGrid when configured
   - SMS through Twilio Messaging when configured
   - secure web reply UI for inbound client messages
6. If realtime behavior changes, update both the JSON thread endpoints and the WebSocket token/session checks together.
7. Add or extend tests in `tests/test_web_dashboard.py`.
8. Update `06_interfaces.md` and setup docs when the messaging contract changes.

## Extend Meeting Lifecycle

1. Keep consultant-facing meeting UX in `core/web.py` and the `templates/consultant/meeting_*.html` files.
2. Put meeting schema/query work in `core/db.py` and `core/schema.sql`.
3. Keep shared meeting helpers in `core/meetings.py`.
4. Treat `client_access_links` as the hosted client response trust anchor for phase 1; do not add raw meeting tokens to browser forms or SQLite metadata.
5. Keep live media authorization in `POST /internal/authorize-meeting-join`, not in the public web routes.
6. Preserve the current split:
   - dashboard web routes schedule, resend, cancel, and host the client response page
   - `simple-backend` mints RTC/RTM only after signed internal authorization succeeds
   - `server-custom-llm` posts deterministic meeting artifacts back through `POST /internal/session-complete`
   - reminder delivery is triggered by `POST /internal/run-reminders`, usually via `scripts/run_reminders.py` from cron
7. Add or extend tests in both:
   - `tests/test_web_dashboard.py`
   - `tests/test_internal_api.py`
8. Update `06_interfaces.md` and `02_architecture.md` when the meeting contract changes.

## Run Meeting Reminders

1. Keep reminder logic inside the dashboard service.
2. Trigger it through the signed internal endpoint:
   - `POST /internal/run-reminders`
3. Use the checked-in helper:
   - `scripts/run_reminders.py`
4. Run it from cron or another scheduler every minute.
5. Keep sends idempotent through `reminder_24h_sent_at` and `reminder_1m_sent_at`.
6. Do not rely on an in-process sleep loop inside the Flask web server.

## Change Client Identity Matching

1. Keep the normal flow UI-driven: consultant creates or edits the client in the dashboard.
2. Update hashing or matching logic in `core/db.py` and any related helper in `app.py`.
3. Preserve the current contract: `simple-backend` sends hashed Google/email/name/phone fields and receives a single `client_id`.
4. Do not move normal onboarding back to CLI-only helpers.
5. Add or extend tests in `tests/test_internal_api.py` and `tests/test_web_dashboard.py`.

## Add a New Table or Field

1. Change `core/schema.sql`.
2. Add helper functions or extend queries in `core/db.py`.
3. Update code paths that read/write the new data.
4. If the field is sensitive, decide whether it belongs in SQLite or encrypted artifacts.
5. Update `06_interfaces.md` and `08_security.md` if the boundary changes.
6. Re-run the full test suite.

## Add a New Session Ingestion Field

1. Extend the payload handling in `core/internal_api.py`.
2. Decide whether it belongs in:
   - `sessions` row
   - `session_alerts`
   - encrypted artifact payload
3. Preserve idempotent `upsert_session()` behavior.
4. If it affects consultant-visible UI, update `core/web.py` and templates.
5. Update `06_interfaces.md`.

If the field is derived from live safety or biomarker streams, document whether the dashboard should store:

- current/latest state
- worst state seen during the call
- rolling baseline comparison

For summary-like fields, keep the current ownership model explicit:

- `server-custom-llm` generates:
  - session key point summary
  - updated AI or human client key point summary
- `consultant_dashboard` stores and renders those artifacts
- `consultant_dashboard` still derives counts and baseline aggregates, but not the long-lived AI/human key point summary text itself

## Add a New Login Rule

1. Modify `core/auth.py`.
2. Keep consultant/admin separation explicit.
3. Update audit logging for success/failure cases.
4. If Twilio behavior changes, test both dev-mode and production-style branches conceptually.
5. Update `07_gotchas.md` if the change is easy to misconfigure.

## Use Local Support Login For Server-Side QA

Use this only when you are on the dashboard host itself and need to inspect the live consultant UI without going through the normal OTP flow.

Current route:

- `http://127.0.0.1:8090/v/<vendor>/consultant/local-support-login`

Hard requirements:

- request must originate from `127.0.0.1` or `::1`
- `CONSULTANT_LOCAL_SUPPORT_LOGIN_ENABLED=true`
- `CONSULTANT_LOCAL_SUPPORT_LOGIN_SECRET` must be set
- you must log in as a real consultant email that exists on that vendor

Safe usage pattern:

1. Enable the env vars in the repo-local `.env` on the server.
2. Restart PM2:
   - `pm2 restart consultant-dashboard`
3. Open the route locally or use `curl` from the server.
4. Confirm the route redirects to `/consultant/dashboard`.
5. Disable it again after use if it is no longer needed.

This route should never be exposed as a remote bypass and should never create a fake consultant identity.

## Do A Browser-Level UI Check On The Server

If the live consultant page needs a real rendered layout check and the repo does not already have Playwright installed locally:

1. Create a disposable harness:
   - `mkdir -p /tmp/pwcheck`
   - `cd /tmp/pwcheck`
   - `npm init -y`
   - `npm install playwright@1.59.1 --no-save`
2. Use the local support login route to create a real consultant session.
3. Open the target consultant page in Playwright and inspect:
   - panel heights
   - computed font sizes / line heights
   - scroll container behavior
4. Save a screenshot to `/tmp/...` if needed for comparison.

This is useful when CSS behavior on the live page is not obvious from server-rendered HTML alone.

## Deferred Hardening / Roadmap Work

Keep deferred security and data-integrity items visible here until they are implemented.

Current notable deferred items:

- add CSRF protection for dashboard write routes
- move session deletion from hard delete to soft delete with audit-friendly semantics
- add admin OTP / second factor
- add rate limiting or brute-force throttling
- add notification presence/unread-aware delivery so every live chat message is not immediately emailed/SMSed

## Run a Basic Local Verification

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
python run.py serve
```

Then exercise:

- `/health`
- consultant login
- admin login
- signed `GET /internal/resolve-client`
- signed `POST /internal/authorize-meeting-join`
- signed `POST /internal/session-complete`
- signed `GET /internal/client-context`
- consultant meeting scheduling and hosted response pages

Targeted runs:

```bash
python -m unittest tests.test_internal_api -v
python -m unittest tests.test_web_dashboard -v
python -m unittest tests.test_smoke -v
```

## Related Deep Dives

- [Therapy Stack Setup](L2/therapy_stack_setup.md) — full local bring-up and troubleshooting flow
