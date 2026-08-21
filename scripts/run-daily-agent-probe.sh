#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-therapy}"
PROMPT="${DAILY_AGENT_PROBE_PROMPT:-Say hello and tell me the time.}"
OUT_DIR="${DAILY_AGENT_PROBE_DIR:-$ROOT_DIR/logs/agent-probes}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_DIR="$(mktemp -d)"
mkdir -p "$OUT_DIR"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

RTM_OUT="$TMP_DIR/rtm.json"
VOICE_OUT="$TMP_DIR/voice.json"

RTM_OK=0
VOICE_OK=0

if "$ROOT_DIR/scripts/run-rtm-test.sh" "$PROFILE" "$PROMPT" 15 >"$RTM_OUT" 2>&1; then
  RTM_OK=1
fi

if "$ROOT_DIR/scripts/run-voice-probe.sh" "$PROFILE" "" 12 >"$VOICE_OUT" 2>&1; then
  VOICE_OK=1
fi

python3 - <<'PY' "$STAMP" "$PROFILE" "$PROMPT" "$RTM_OK" "$VOICE_OK" "$RTM_OUT" "$VOICE_OUT" >"$OUT_DIR/$STAMP.json"
import json
import sys
from pathlib import Path

stamp, profile, prompt, rtm_ok, voice_ok, rtm_path, voice_path = sys.argv[1:]

def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace").strip()

payload = {
    "timestamp_utc": stamp,
    "profile": profile,
    "prompt": prompt,
    "rtm_ok": rtm_ok == "1",
    "voice_ok": voice_ok == "1",
    "rtm_output": read_text(rtm_path),
    "voice_output": read_text(voice_path),
}
print(json.dumps(payload, indent=2))
PY

cat "$OUT_DIR/$STAMP.json"

if [[ "$RTM_OK" -eq 1 && "$VOICE_OK" -eq 1 ]]; then
  exit 0
fi

exit 1
