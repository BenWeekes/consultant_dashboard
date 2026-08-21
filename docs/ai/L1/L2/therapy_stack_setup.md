# Therapy Stack Setup

This is the operational setup runbook for the full local MindFix stack.

It covers the repos that need to be running together:

- `consultant-dashboard`
- `agent-samples/simple-backend`
- `agent-samples/react-video-client-avatar`
- `server-custom-llm`

It also covers the dependent runtime pieces:

- quick or named tunnel for the public `chat/completions` URL
- reminder cron entry
- dashboard/client/backend/custom-LLM health checks

## Repos

Working tree root:

- `/Users/benweekes/work/therapy`

Main repos:

- `/Users/benweekes/work/therapy/consultant-dashboard`
- `/Users/benweekes/work/therapy/agent-samples`
- `/Users/benweekes/work/therapy/server-custom-llm`

## Startup Order

Bring the stack up in this order:

1. `server-custom-llm`
2. public tunnel to `server-custom-llm`
3. `agent-samples/simple-backend`
4. `consultant-dashboard`
5. `react-video-client-avatar`

Why this order:

- the backend needs the live public `THERAPY_LLM_URL`
- AI session startup fails if Agora receives a dead tunnel hostname
- dashboard-backed auth and meeting joins fail if `:8090` is down

## Required Endpoints

Expected local endpoints:

- dashboard: `http://127.0.0.1:8090`
- backend: `http://127.0.0.1:8082`
- client: `http://localhost:8084`
- custom LLM: `http://127.0.0.1:8101`

Expected health checks:

- dashboard: `http://127.0.0.1:8090/health`
- backend: `http://127.0.0.1:8082/health`
- custom LLM: `http://127.0.0.1:8101/ping`

## Key Environment Variables

### `consultant-dashboard`

Important local config:

- `CONSULTANT_INTERNAL_SHARED_SECRET`
- `CONSULTANT_DASHBOARD_URL`
- `CONSULTANT_SENDGRID_API_KEY`
- `CONSULTANT_EMAIL_FROM`
- `CONSULTANT_EMAIL_REPLY_TO`
- `CONSULTANT_TWILIO_ACCOUNT_SID`
- `CONSULTANT_TWILIO_AUTH_TOKEN`
- `CONSULTANT_TWILIO_VERIFY_SERVICE_SID`
- `CONSULTANT_TWILIO_MESSAGING_SERVICE_SID`
- `CONSULTANT_PUBLIC_BASE_URL`

### `agent-samples/simple-backend`

Important therapy profile config:

- `THERAPY_LLM_URL`
  - public URL used by Agora ConvoAI for `chat/completions`
- `THERAPY_AGENT_SERVER_URL`
  - local control URL used for `/register-agent` and `/unregister-agent`
  - typically `http://127.0.0.1:8101`
- `THERAPY_CONSULTANT_DASHBOARD_URL`
  - typically `http://127.0.0.1:8090`
- `THERAPY_CONSULTANT_DASHBOARD_INTERNAL_SHARED_SECRET`
- `THERAPY_AGENT_SERVER_SHARED_SECRET`
- `THERAPY_CUSTOM_LLM_INBOUND_SECRET`
  - dedicated Bearer credential passed by Agora as `properties.llm.api_key`
  - must match custom-LLM `CUSTOM_LLM_INBOUND_SECRET`
  - must not be the OpenAI provider key

### `server-custom-llm`

Important local config:

- `LLM_API_KEY`
  - server-side OpenAI provider credential; never supplied in the ConvoAI join payload
- `CUSTOM_LLM_INBOUND_SECRET`
  - validates ConvoAI Bearer requests; must match the backend profile value
- `AGORA_CUSTOMER_ID`
- `AGORA_CUSTOMER_SECRET`
- any integration keys such as Thymia

## Start Commands

### `server-custom-llm`

Run from:

- `/Users/benweekes/work/therapy/server-custom-llm/node`

Typical local command:

```bash
node custom_llm.js
```

Health check:

```bash
curl http://127.0.0.1:8101/ping
```

### Tunnel

Quick tunnel example:

```bash
cloudflared tunnel --url http://127.0.0.1:8101
```

Use the emitted `https://...trycloudflare.com` URL in:

- `THERAPY_LLM_URL=https://<tunnel>/chat/completions`

Verify:

```bash
curl https://<tunnel>/ping
```

### `simple-backend`

Run from:

- `/Users/benweekes/work/therapy/agent-samples/simple-backend`

Typical local command:

```bash
PYTHONUNBUFFERED=1 ./venv/bin/python local_server.py
```

Health check:

```bash
curl http://127.0.0.1:8082/health
```

### `consultant-dashboard`

Run from:

- `/Users/benweekes/work/therapy/consultant-dashboard`

Typical local command:

```bash
PYTHONUNBUFFERED=1 ./venv/bin/python run.py serve
```

Health check:

```bash
curl http://127.0.0.1:8090/health
```

### `react-video-client-avatar`

Run from:

- `/Users/benweekes/work/therapy/agent-samples/react-video-client-avatar`

Use the repo’s normal dev command so `http://localhost:8084` is available.

## Reminder Job

The dashboard owns reminder delivery.

Run manually:

```bash
cd /Users/benweekes/work/therapy/consultant-dashboard
./venv/bin/python scripts/run_reminders.py
```

Cron example:

```cron
* * * * * cd /Users/benweekes/work/therapy/consultant-dashboard && ./venv/bin/python scripts/run_reminders.py --quiet >> /tmp/mindfix-reminders.log 2>&1
```

## Before Testing

Check all four dependencies:

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8082/health
curl http://127.0.0.1:8101/ping
curl https://<current-tunnel>/ping
```

Then verify the backend is actually configured with the live tunnel:

```bash
rg '^THERAPY_LLM_URL=' /Users/benweekes/work/therapy/agent-samples/simple-backend/.env
```

For AI sessions, the latest Agora curl dump is the source of truth:

```bash
ls -1t /tmp/agora_curl_therapy_*.sh | head -n 1
```

Open the newest file and confirm the `llm.url` matches the live tunnel.

## Common Failure Modes

### Dead tunnel

Symptoms:

- AI says `Sorry, something went wrong`
- latest Agora payload points at a dead `trycloudflare.com` hostname

Fix:

1. start a fresh tunnel
2. update `THERAPY_LLM_URL`
3. restart backend on `:8082`
4. verify the latest curl dump contains the new URL

### Custom-LLM authentication mismatch

Symptoms:

- AI says `Sorry, something went wrong`
- custom-LLM logs `Rejected custom LLM request with invalid Bearer credential`

Fix:

1. set the same dedicated secret in `THERAPY_CUSTOM_LLM_INBOUND_SECRET` and `CUSTOM_LLM_INBOUND_SECRET`
2. keep `LLM_API_KEY` only in the custom-LLM environment
3. restart custom-LLM, then simple-backend
4. run `scripts/run-daily-agent-probe.sh therapy`

### Dashboard unavailable

Symptoms:

- Google or password auth reaches SMS step with:
  - `Dashboard authorization is temporarily unavailable. Please try again.`

Fix:

1. verify `http://127.0.0.1:8090/health`
2. restart dashboard if needed

### Stale bound process

Symptoms:

- behavior does not match code on disk
- page still renders old template
- backend still returns an old error path after a code fix

Fix:

1. identify the exact listening PID
2. kill it
3. restart the intended service
4. verify `/health`
5. only then trust the result

Useful commands:

```bash
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:8082 -sTCP:LISTEN
```

## AI Sessions vs Human Meetings

### AI session

- user talks to the AI agent
- Agora needs the public `THERAPY_LLM_URL`
- artifacts posted back to dashboard at session end:
  - summary
  - biomarkers

### Human meeting

- consultant and client join the same persistent pair room
- `simple-backend` uses local `THERAPY_AGENT_SERVER_URL` for control calls
- `server-custom-llm` runs:
  - audio subscriber
  - Thymia
  - optional Agora STT
- artifacts posted back to dashboard at meeting end:
  - summary
  - biomarkers
  - transcript when STT is enabled

## Signal Controls

For human meetings:

- `Speech-to-Text`
- `Audio Biomarkers`
- `Video Biomarkers`

Current defaults:

- speech-to-text: on for the current product flow
- audio biomarkers: on
- video biomarkers: on when `SHEN_AVAILABLE=true`; forcibly off for new sessions when `SHEN_AVAILABLE=false`

Session pages should clearly distinguish:

- not enabled
- enabled but missing
- present

## Related Docs

- [L1 Setup](../01_setup.md)
- [L1 Architecture](../02_architecture.md)
- [L1 Code Map](../03_code_map.md)
- [L1 Gotchas](../07_gotchas.md)
- `/Users/benweekes/work/therapy/agent-samples/recipes/therapist.md`
- `/Users/benweekes/work/therapy/meeting_codex_plan.md`
- `/Users/benweekes/work/therapy/codex_channel_plan.md`
- `/Users/benweekes/work/therapy/meeting_signal_controls_codex_plan.md`
