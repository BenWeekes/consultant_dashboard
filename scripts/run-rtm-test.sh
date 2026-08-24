#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-therapy}"
PROMPT="${2:-Say hello and tell me the time.}"
TIMEOUT_SECS="${3:-12}"
EXPECTED_TEXT="${4:-}"
EXPECTED_PROBE_NONCE="${EXPECTED_TEXT:-$PROMPT}"
BACKEND_BASE="${BACKEND_BASE:-http://127.0.0.1:8082}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"

START_JSON=""
if ! START_JSON="$("$PYTHON_BIN" "$ROOT_DIR/scripts/agent_probe_backend.py" start \
  --profile "$PROFILE" \
  --backend-base "$BACKEND_BASE" \
  --greeting "" \
)"; then
  printf '%s\n' "$START_JSON"
  exit 1
fi

if [[ "$(printf '%s' "$START_JSON" | "$PYTHON_BIN" -c 'import json,sys; print("1" if json.load(sys.stdin).get("ok") else "0")')" != "1" ]]; then
  printf '%s\n' "$START_JSON"
  exit 1
fi

RESULT_JSON="$("$PYTHON_BIN" - <<'PY' "$START_JSON"
import json, sys
payload = json.loads(sys.argv[1])["result"]
print(json.dumps({
    "app_id": payload["appid"],
    "channel": payload["channel"],
    "token": payload["token"],
    "uid": str(payload.get("user_rtm_uid") or payload["uid"]),
    "agent_uid": str(payload.get("agent_rtm_uid") or payload.get("agent", {}).get("uid") or ""),
    "agent_id": payload["agent_id"],
}))
PY
)"

APP_ID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["app_id"])')"
CHANNEL="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["channel"])')"
TOKEN="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
RTM_UID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["uid"])')"
AGENT_ID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')"
AGENT_RTM_UID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["agent_uid"])')"
PROBE_AUTH_TOKEN="$(printf '%s' "$START_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["result"]["probe_auth_token"])')"
CHANNEL_FROM_START="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["channel"])')"

cleanup() {
  "$PYTHON_BIN" "$ROOT_DIR/scripts/agent_probe_backend.py" stop \
    --profile "$PROFILE" \
    --backend-base "$BACKEND_BASE" \
    --agent-id "$AGENT_ID" \
    --channel "$CHANNEL_FROM_START" \
    --auth-token "$PROBE_AUTH_TOKEN" >/dev/null 2>&1 || true
}
trap cleanup EXIT

set +e
node "$ROOT_DIR/scripts/rtm_probe.js" \
  "$APP_ID" \
  "$CHANNEL" \
  "$TOKEN" \
  "$RTM_UID" \
  "$AGENT_RTM_UID" \
  "$PROMPT" \
  "$TIMEOUT_SECS" \
  "1500" \
  "0" \
  "$EXPECTED_TEXT"
RTM_STATUS=$?
set -e

if [[ "$RTM_STATUS" -eq 0 ]]; then
  exit 0
fi

# A peer RTM injection can reach ConvoAI and produce a successful custom-LLM
# completion without ConvoAI echoing the assistant text back over RTM. Confirm
# that exact request/response pair through the custom server's authenticated,
# local diagnostic endpoint rather than accepting a timeout as healthy.
CUSTOM_LLM_SECRET="${CUSTOM_LLM_SECRET:-$(PROFILE_FOR_PROBE="$PROFILE" BACKEND_ENV_PATH="$ROOT_DIR/../agent-samples/simple-backend/.env" "$PYTHON_BIN" - <<'PY'
from dotenv import dotenv_values
import os
profile = os.environ.get('PROFILE_FOR_PROBE', 'therapy').upper()
values = dotenv_values(os.environ['BACKEND_ENV_PATH'])
print(values.get(f'{profile}_CUSTOM_LLM_INBOUND_SECRET') or '')
PY
)}"
if [[ -n "$CUSTOM_LLM_SECRET" ]]; then
  STATUS_JSON="$(curl -fsS --get \
    --data-urlencode "channel=$CHANNEL" \
    --data-urlencode "nonce=$EXPECTED_PROBE_NONCE" \
    -H "Authorization: Bearer $CUSTOM_LLM_SECRET" \
    "${CUSTOM_LLM_BASE:-http://127.0.0.1:8101}/probe/completion-status" 2>/dev/null || true)"
  if [[ -n "$STATUS_JSON" ]] && "$PYTHON_BIN" - <<'PY' "$STATUS_JSON"
import json, sys
payload = json.loads(sys.argv[1])
raise SystemExit(0 if payload.get('ok') else 1)
PY
  then
    printf '%s\n' "$STATUS_JSON"
    exit 0
  fi
fi

exit "$RTM_STATUS"
