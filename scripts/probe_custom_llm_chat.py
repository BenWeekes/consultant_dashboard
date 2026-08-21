#!/usr/bin/env python3
"""Exercise custom LLM chat authentication and its upstream provider call."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--inbound-secret", default=os.environ.get("CUSTOM_LLM_INBOUND_SECRET", ""))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if not args.inbound_secret:
        print(json.dumps({"ok": False, "error": "--inbound-secret is required"}))
        return 2

    payload: dict[str, Any] = {
        "model": args.model,
        "stream": False,
        "messages": [{"role": "user", "content": "Say hello and tell me the time."}],
    }
    if args.reasoning_effort:
        payload["reasoning_effort"] = args.reasoning_effort

    started = time.time()
    req = urllib.request.Request(
        args.url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {args.inbound_secret}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    content = ""
    try:
        content = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    except Exception:
        content = ""

    ok = bool(content) and "something went wrong" not in content.lower()
    print(json.dumps({
        "ok": ok,
        "status": 200,
        "latency_ms": int((time.time() - started) * 1000),
        "content": content,
    }))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
