#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-therapy}"
PROMPT="${2:-Say hello and tell me the time.}"
TIMEOUT_SECS="${3:-12}"
BACKEND_BASE="${BACKEND_BASE:-http://127.0.0.1:8082}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

START_JSON="$("$PYTHON_BIN" "$ROOT_DIR/scripts/agent_probe_backend.py" start \
  --profile "$PROFILE" \
  --backend-base "$BACKEND_BASE" \
  --greeting "" \
)"

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
    "agent_id": payload["agent_id"],
}))
PY
)"

APP_ID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["app_id"])')"
CHANNEL="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["channel"])')"
TOKEN="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
RTM_UID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["uid"])')"
AGENT_ID="$(printf '%s' "$RESULT_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')"

cleanup() {
  "$PYTHON_BIN" "$ROOT_DIR/scripts/agent_probe_backend.py" stop \
    --profile "$PROFILE" \
    --backend-base "$BACKEND_BASE" \
    --agent-id "$AGENT_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

node "$ROOT_DIR/scripts/rtm_probe.js" \
  "$APP_ID" \
  "$CHANNEL" \
  "$TOKEN" \
  "$RTM_UID" \
  "$PROMPT" \
  "$TIMEOUT_SECS"
