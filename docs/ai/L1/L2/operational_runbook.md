# Operational Runbook

This deep dive holds the operational detail trimmed out of L1 setup.

## Reminder Runner

- checked-in helper: `scripts/run_reminders.py`
- dashboard endpoint: `POST /internal/run-reminders`
- intended invocation: cron or another scheduler, not a request-path side effect

Example cron entry:

```cron
* * * * * cd /Users/benweekes/work/therapy/consultant-dashboard && ./venv/bin/python scripts/run_reminders.py --quiet >> /tmp/mindfix-reminders.log 2>&1
```

Rules:

- sends reminders only for future scheduled meetings
- skips immediate `meet now` meetings
- uses signed meeting-response tokens rather than storing raw invite tokens
- relies on `reminder_24h_sent_at` and `reminder_1m_sent_at` for idempotency

## Verification

Full suite:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

Live stack smoke:

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

Local Mac with direct ports:

```bash
RUN_LIVE_STACK_TESTS=1 \
LIVE_BACKEND_URL=http://127.0.0.1:8082 \
LIVE_DASHBOARD_URL=http://127.0.0.1:8090 \
LIVE_CLIENT_URL='http://localhost:8084?profile=therapy&autoconnect=true' \
LIVE_CUSTOM_LLM_URL=http://127.0.0.1:8101 \
python -m unittest tests.test_live_stack -v
```

## Playwright

Run browser e2e checks:

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

## Daily Agent Probes

These probes exercise the live AI session stack without using the browser UI.

RTM text probe:

```bash
cd /home/ubuntu/mindfix/consultant_dashboard
scripts/run-rtm-test.sh therapy "Say hello and tell me the time." 15
```

This starts a real agent through `simple-backend`, authenticates as a real client, sends a user turn over RTM, and waits for the assistant reply. The output includes `latency_ms` and the assistant text.

Audio-out probe:

```bash
cd /home/ubuntu/mindfix/consultant_dashboard
scripts/run-voice-probe.sh therapy "" 12
```

This starts a real agent, subscribes to the agent RTC audio stream with `go-audio-subscriber`, triggers a spoken reply through `/speak`, and confirms the agent responded. It prefers PCM amplitude detection and falls back to assistant transcript stream events if PCM is not exposed on the host.

Combined daily probe:

```bash
cd /home/ubuntu/mindfix/consultant_dashboard
scripts/run-daily-agent-probe.sh therapy
```

This writes a combined JSON record to `logs/agent-probes/<timestamp>.json` and exits non-zero if either probe fails.

Notes:

- `scripts/agent_probe_backend.py` mints a valid therapy client JWT before calling `/start-agent`. By default it uses `AI_PROBE_CLIENT_ID` if set, otherwise the first active client in the dashboard database.
- Override the default prompt with `DAILY_AGENT_PROBE_PROMPT` or `VOICE_PROBE_PROMPT`.
- These probes validate the live orchestration path and agent response path. They do not yet publish a custom WAV utterance into RTC as a full speech-in probe.
