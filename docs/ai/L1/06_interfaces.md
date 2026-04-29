# 06 Interfaces

> Summarize the repo's public and internal interfaces, and point detailed route contracts to L2.

## Web Surface

Primary UI audiences:

- consultants
- admins
- clients using secure message or meeting-response links

High-level route groups:

- consultant auth, dashboard, clients, meetings, sessions
- admin auth, dashboard, consultant management
- client secure-link pages for messages and meeting responses
- shared health and logout routes

Important behaviors:

- consultants and admins have separate login flows
- clients do not use normal dashboard logins
- client secure links are token-bound and expire
- realtime message views use websocket notification plus thread re-fetch

## Internal Service APIs

The dashboard exposes signed internal APIs to `simple-backend` and `server-custom-llm`.

Core contracts:

- `GET /internal/health`
- `GET /internal/resolve-client`
- `GET /internal/client-context`
- `POST /internal/authorize-meeting-join`
- `POST /internal/verify-client-password`
- `POST /internal/run-reminders`
- `POST /internal/session-complete`

Contract expectations:

- all non-health internal routes use HMAC request signing
- dashboard responses are authoritative for client identity and meeting join rights
- browser-supplied meeting ids, participant ids, and UIDs must not override dashboard authorization results

## Realtime Interfaces

Current websocket routes:

- consultant message thread updates
- client secure-thread updates

The websocket layer is notification-oriented rather than full event replay. Pages usually re-fetch canonical JSON after `thread_updated`.

## Stable Data Contracts

Most important payload families:

- client identity resolution
- consultant-facing client context for prompt injection
- meeting join authorization
- reminder sweep results
- session-complete ingestion payloads from `server-custom-llm`

If an integration change affects one of those payloads, update tests in both this repo and the calling service.

## Where The Detailed Route List Lives

Do not expand this file into a route inventory again. Put exact methods, params, payload fields, and notes in L2 so L1 stays readable.

## Related Deep Dives

- [http_interfaces.md](L2/http_interfaces.md)
