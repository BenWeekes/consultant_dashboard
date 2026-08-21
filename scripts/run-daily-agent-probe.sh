#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-therapy}"
OUT_DIR="${DAILY_AGENT_PROBE_DIR:-$ROOT_DIR/logs/agent-probes}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NONCE="MINDFIX_PROBE_OK_${STAMP}"
PROMPT="${DAILY_AGENT_PROBE_PROMPT:-This is an automated service health check. Reply with exactly: $NONCE}"
EXPECTED_TEXT="${DAILY_AGENT_PROBE_EXPECTED_TEXT:-$NONCE}"
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

# This is the decisive LLM check: a real ConvoAI agent must call custom-LLM
# and return the unique nonce. Failure-message audio or stale RTM traffic fails.
if "$ROOT_DIR/scripts/run-rtm-test.sh" \
  "$PROFILE" "$PROMPT" 30 "$EXPECTED_TEXT" >"$RTM_OUT" 2>&1; then
  RTM_OK=1
fi

# Separately verify that the live agent can produce outbound audio.
if "$ROOT_DIR/scripts/run-voice-probe.sh" "$PROFILE" "" 15 >"$VOICE_OUT" 2>&1; then
  VOICE_OK=1
fi

"$PYTHON_BIN" - <<'PY' \
  "$STAMP" "$PROFILE" "$PROMPT" "$EXPECTED_TEXT" \
  "$RTM_OK" "$VOICE_OK" "$RTM_OUT" "$VOICE_OUT" >"$OUT_DIR/$STAMP.json"
import json
import sys
from pathlib import Path

stamp, profile, prompt, expected, rtm_ok, voice_ok, rtm_path, voice_path = sys.argv[1:]

def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace").strip()

def last_json(path: str):
    for line in reversed(read_text(path).splitlines()):
        try:
            return json.loads(line)
        except Exception:
            continue
    return None

rtm = last_json(rtm_path)
voice = last_json(voice_path)
rtm_passed = (
    rtm_ok == "1"
    and isinstance(rtm, dict)
    and rtm.get("ok") is True
    and expected.lower() in str(rtm.get("response") or "").lower()
    and "something went wrong" not in str(rtm.get("response") or "").lower()
)
voice_passed = voice_ok == "1" and isinstance(voice, dict) and voice.get("agent_spoke") is True
failures = []
if not rtm_passed:
    failures.append("custom LLM did not return the expected response through ConvoAI")
if not voice_passed:
    failures.append("agent outbound audio was not detected")

payload = {
    "timestamp_utc": stamp,
    "profile": profile,
    "passed": rtm_passed and voice_passed,
    "checks": {
        "convoai_llm_response": {
            "passed": rtm_passed,
            "latency_ms": rtm.get("latency_ms") if isinstance(rtm, dict) else None,
            "response": rtm.get("response") if isinstance(rtm, dict) else None,
            "error": rtm.get("error") if isinstance(rtm, dict) else read_text(rtm_path),
        },
        "outbound_audio": {
            "passed": voice_passed,
            "peak_rms": voice.get("peak_rms") if isinstance(voice, dict) else None,
            "voiced_frames": voice.get("voiced_frames") if isinstance(voice, dict) else None,
            "transcript_events": voice.get("transcript_events") if isinstance(voice, dict) else None,
            "error": None if isinstance(voice, dict) else read_text(voice_path),
        },
    },
    "failures": failures,
}
print(json.dumps(payload, indent=2))
PY

cat "$OUT_DIR/$STAMP.json"

PASSED="$($PYTHON_BIN - <<'PY' "$OUT_DIR/$STAMP.json"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print("1" if payload.get("passed") else "0")
PY
)"

if [[ "$PASSED" -eq 1 ]]; then
  exit 0
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/send_probe_alert.py" "$OUT_DIR/$STAMP.json" || true
exit 1
