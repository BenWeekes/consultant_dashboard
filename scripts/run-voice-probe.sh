#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-therapy}"
WAV_PATH="${2:-}"
LISTEN_SECS="${3:-12}"
PROMPT="${VOICE_PROBE_PROMPT:-Say hello and tell me the time.}"
BACKEND_BASE="${BACKEND_BASE:-http://127.0.0.1:8082}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"

if [[ -n "$WAV_PATH" ]]; then
  echo "warning: explicit WAV input is not wired yet in the private probe; using backend-triggered audio-out confirmation instead." >&2
fi

GO_DIR="$ROOT_DIR/../server-custom-llm/go-audio-subscriber"
GO_BIN="$GO_DIR/bin/go-audio-subscriber"
if [[ ! -x "$GO_BIN" ]]; then
  mkdir -p "$GO_DIR/bin"
  (cd "$GO_DIR" && go build -o "$GO_BIN" .)
fi

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

SESSION_JSON="$(printf '%s' "$START_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["result"]))')"
AGENT_ID="$(printf '%s' "$SESSION_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["agent_id"])')"
CHANNEL="$(printf '%s' "$SESSION_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["channel"])')"

cleanup() {
  "$PYTHON_BIN" "$ROOT_DIR/scripts/agent_probe_backend.py" stop \
    --profile "$PROFILE" \
    --backend-base "$BACKEND_BASE" \
    --agent-id "$AGENT_ID" \
    --channel "$CHANNEL" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$PYTHON_BIN" "$ROOT_DIR/scripts/audio_reply_probe.py" \
  --session-json "$SESSION_JSON" \
  --listen-seconds "$LISTEN_SECS" \
  --prompt "$PROMPT" \
  --backend-base "$BACKEND_BASE" \
  --profile "$PROFILE"
