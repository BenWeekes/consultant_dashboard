#!/usr/bin/env python3
"""Subscribe to agent RTC audio and confirm the agent spoke."""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from base64 import b64decode
from pathlib import Path
from typing import Any


FRAME_JSON = 0x01
FRAME_PCM = 0x02
RMS_THRESHOLD = 250.0
MIN_VOICED_FRAMES = 12
MIN_TRANSCRIPT_EVENTS = 3


def _read_exact(stream, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            raise EOFError("unexpected EOF from go-audio-subscriber")
        buf.extend(chunk)
    return bytes(buf)


def _frame_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    sample_count = len(pcm) // 2
    fmt = f"<{sample_count}h"
    samples = struct.unpack(fmt, pcm[: sample_count * 2])
    if not samples:
        return 0.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return math.sqrt(mean_square)


def _decode_stream_message(encoded: str) -> dict[str, Any] | None:
    try:
        raw = b64decode(encoded).decode("utf-8", errors="replace")
        parts = raw.split("|")
        if not parts:
            return None
        payload = json.loads(b64decode(parts[-1]).decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _run_rtm_probe(
    *,
    repo_root: Path,
    app_id: str,
    channel: str,
    token: str,
    uid: str,
    prompt: str,
    timeout_seconds: int,
    hold_after_response_ms: int,
) -> dict[str, Any]:
    command = [
        "node",
        str(repo_root / "scripts" / "rtm_probe.js"),
        app_id,
        channel,
        token,
        uid,
        prompt,
        str(timeout_seconds),
        "0",
        str(hold_after_response_ms),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip().splitlines()
    if not stdout:
        raise RuntimeError(f"rtm probe produced no output: stderr={completed.stderr.strip()}")
    result = json.loads(stdout[-1])
    if completed.returncode != 0 or not result.get("ok"):
        raise RuntimeError(f"rtm probe failed: {result}")
    return result


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-json", required=True)
    parser.add_argument("--listen-seconds", type=int, default=12)
    parser.add_argument("--prompt", default="Say hello and tell me the time.")
    parser.add_argument("--backend-base", default="http://127.0.0.1:8082")
    parser.add_argument("--profile", default="therapy")
    args = parser.parse_args()

    session = json.loads(args.session_json)
    repo_root = Path(__file__).resolve().parents[1]
    go_dir = repo_root.parent / "server-custom-llm" / "go-audio-subscriber"
    go_binary = go_dir / "bin" / "go-audio-subscriber"
    ld_library_path = str(go_dir / "sdk" / "agora_sdk")

    if not go_binary.exists():
      raise RuntimeError(f"missing go-audio-subscriber binary at {go_binary}")

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = ld_library_path + (
        f":{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else ""
    )

    proc = subprocess.Popen(
        [str(go_binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=False,
        cwd=str(go_dir),
    )

    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            try:
                stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
            except Exception:
                stderr_lines.append(repr(line))

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    assert proc.stdin is not None
    assert proc.stdout is not None

    start_cmd = {
        "type": "start",
        "appId": session["appid"],
        "channel": session["channel"],
        "botUid": str(session.get("uid")),
        "token": session["token"],
        "targetUid": str(session["agent"]["uid"]),
    }
    proc.stdin.write((json.dumps(start_cmd) + "\n").encode("utf-8"))
    proc.stdin.flush()

    peak_rms = 0.0
    voiced_frames = 0
    statuses: list[dict[str, Any]] = []
    ready = False
    speak_result: dict[str, Any] | None = None
    transcript_events = 0
    transcript_texts: list[str] = []
    deadline = time.time() + max(5, args.listen_seconds + 10)
    listen_deadline: float | None = None

    try:
        while time.time() < deadline:
            ready_streams, _, _ = select.select([proc.stdout], [], [], 0.25)
            if not ready_streams:
                if ready and listen_deadline is not None and time.time() >= listen_deadline:
                    break
                continue

            frame_type = _read_exact(proc.stdout, 1)[0]
            frame_length = int.from_bytes(_read_exact(proc.stdout, 4), "big")
            payload = _read_exact(proc.stdout, frame_length)

            if frame_type == FRAME_JSON:
                message = json.loads(payload.decode("utf-8"))
                if message.get("type") != "stream_message":
                    statuses.append({
                        key: message[key]
                        for key in ("type", "status", "message", "uid")
                        if key in message
                    })
                if message.get("type") == "stream_message" and str(message.get("uid")) == str(session["agent"]["uid"]):
                    decoded = _decode_stream_message(str(message.get("data") or ""))
                    if decoded and decoded.get("object") == "assistant.transcription":
                        transcript_events += 1
                        text = str(decoded.get("text") or "").strip()
                        if text:
                            transcript_texts.append(text)
                status = message.get("status")
                if status == "ready" and not ready:
                    ready = True
                    listen_deadline = time.time() + args.listen_seconds
                    speak_result = _post_json(
                        f"{args.backend_base.rstrip('/')}/speak",
                        {
                            "agent_id": session["agent_id"],
                            "profile": args.profile,
                            "text": args.prompt,
                            "priority": "INTERRUPT",
                        },
                    )
                elif status == "target_left":
                    break
            elif frame_type == FRAME_PCM:
                rms = _frame_rms(payload)
                if rms > peak_rms:
                    peak_rms = rms
                if rms >= RMS_THRESHOLD:
                    voiced_frames += 1

            if ready and listen_deadline is not None and time.time() >= listen_deadline:
                break
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(b'{"type":"stop"}\n')
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    success = (
        (voiced_frames >= MIN_VOICED_FRAMES and peak_rms >= RMS_THRESHOLD)
        or transcript_events >= MIN_TRANSCRIPT_EVENTS
    )
    result = {
        "ok": success,
        "agent_spoke": success,
        "channel": session["channel"],
        "peak_rms": round(peak_rms, 2),
        "voiced_frames": voiced_frames,
        "threshold_rms": RMS_THRESHOLD,
        "min_voiced_frames": MIN_VOICED_FRAMES,
        "transcript_events": transcript_events,
        "min_transcript_events": MIN_TRANSCRIPT_EVENTS,
        "transcript_preview": transcript_texts[:5],
        "speak_result": speak_result,
        "statuses": statuses[-10:],
    }
    if stderr_lines:
        result["stderr_tail"] = stderr_lines[-10:]

    print(json.dumps(result))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
