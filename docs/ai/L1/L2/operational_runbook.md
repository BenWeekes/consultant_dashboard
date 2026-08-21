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
scripts/run-rtm-test.sh therapy \
  "Reply with exactly: MINDFIX_PROBE_OK_MANUAL" 30 \
  "MINDFIX_PROBE_OK_MANUAL"
```

This starts a real agent through `simple-backend`, authenticates as the dedicated synthetic probe client, sends a user turn over RTM, and waits for the expected assistant reply. It rejects known failure responses such as `Sorry, something went wrong`. The output includes `latency_ms` and the assistant text.

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

This uses a unique nonce on each run, writes a concise JSON record to `logs/agent-probes/<timestamp>.json`, and exits non-zero if either check fails:

- authenticated `/start-agent` succeeds for the synthetic client and ConvoAI returns the exact nonce through custom-LLM
- the audio probe confirms the agent spoke back

On failure it also attempts to email every dashboard admin listed in `CONSULTANT_ADMIN_AUTH_FILE` using the normal SendGrid delivery path.

Notes:

- `AI_PROBE_CLIENT_ID` is required. It must identify a dedicated active client under a consultant with `AI Testing Mode` enabled and must have client AI escalation disabled. The probe refuses to use arbitrary real clients.
- The payload must include Agora's top-level `properties.llm.api_key`, but its value is a dedicated custom-LLM inbound secret, never the OpenAI provider key.
- Override both `DAILY_AGENT_PROBE_PROMPT` and `DAILY_AGENT_PROBE_EXPECTED_TEXT` together when changing the nonce assertion. `VOICE_PROBE_PROMPT` controls only the audio-out check.
- These probes validate the live orchestration path and agent response path. They do not yet publish a custom WAV utterance into RTC as a full speech-in probe.

Production cron:

```cron
17 6 * * * flock -n /tmp/mindfix-daily-agent-probe.lock /home/ubuntu/mindfix/consultant_dashboard/scripts/run-daily-agent-probe.sh therapy >> /tmp/mindfix-daily-agent-probe.log 2>&1
```

The lock prevents overlapping agents if a run stalls. Failure sends an email to configured dashboard admins; success is recorded without email.
