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

This starts a real agent through `simple-backend`, authenticates as the dedicated synthetic probe client, and sends the ConvoAI peer-message envelope over RTM. It rejects known failure responses such as `Sorry, something went wrong`. If ConvoAI completes the turn through custom-LLM but does not echo assistant text over RTM, the probe verifies the exact nonce through the custom server's authenticated local diagnostic endpoint. The output includes `latency_ms` and the assistant text.

Audio-out probe:

```bash
cd /home/ubuntu/mindfix/consultant_dashboard
scripts/run-voice-probe.sh therapy "" 25
```

This starts a real agent, subscribes to the agent RTC audio stream with `go-audio-subscriber`, triggers a spoken reply through `/speak`, and requires non-silent PCM from the agent. Transcript events alone do not pass this check, because they can exist while outbound audio is silent.

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
- A failed outbound-audio check is a real health failure, even when the custom-LLM nonce check passes. Investigate Agora TTS/RTC delivery before treating the system as fully healthy.
- The audio probe subscribes to the avatar's video/audio UID (`102`), not the conversational agent control UID (`100`). The combined daily probe listens for 25 seconds by default; override with `DAILY_AGENT_AUDIO_LISTEN_SECONDS` when diagnosing slow TTS startup.

Production cron:

```cron
17 6 * * * flock -n /tmp/mindfix-daily-agent-probe.lock /home/ubuntu/mindfix/consultant_dashboard/scripts/run-daily-agent-probe.sh therapy >> /tmp/mindfix-daily-agent-probe.log 2>&1
```

The lock prevents overlapping agents if a run stalls. Failure sends an email to configured dashboard admins; success is recorded without email.

## Prompt And Model Promotion

- `evals/prompts/candidate_v3_no_external.txt` is an evaluation candidate, not the live prompt. Do not promote its blanket ban on crisis lines, emergency services, or other external support: immediate-risk guidance must retain the configured region-aware support routes.
- Prompt changes must be reviewed against crisis, self-harm, violence, poisoning, substance misuse, secrecy, and continuity cases before changing the ignored runtime `.env` values.
- A model name appearing in eval result files is not production approval. Confirm that the model is available to the configured provider, then compare safety, engagement, and latency on the platform path before changing `THERAPY_LLM_MODEL` or `LLM_MODEL`.
- The dashboard context tells the model to follow the client for the first 3-4 meaningful turns before using older notes to redirect a drifting conversation. The `ai_escalation_enabled` flag changes the internal safety guidance without exposing the platform's implementation to the client.
