#!/usr/bin/env python3
"""Trigger the dashboard reminder sweep via the signed internal endpoint."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_BASE_URL = "http://127.0.0.1:8090"
DEFAULT_PATH = "/internal/run-reminders"


def load_secret(env_path: Path) -> str:
    if not env_path.exists():
        raise SystemExit(f"env file not found: {env_path}")
    for line in env_path.read_text().splitlines():
        if line.startswith("CONSULTANT_INTERNAL_SHARED_SECRET="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(
        f"CONSULTANT_INTERNAL_SHARED_SECRET not found in {env_path}"
    )


def sign_request(secret: str, timestamp: str, method: str, path: str, payload: str) -> str:
    canonical = f"{timestamp}.{method}.{path}.{payload}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def build_request(base_url: str, path: str, secret: str) -> urllib.request.Request:
    method = "POST"
    payload = ""
    timestamp = str(int(time.time()))
    signature = sign_request(secret, timestamp, method, path, payload)
    url = f"{base_url.rstrip('/')}{path}"
    return urllib.request.Request(
        url,
        data=b"",
        method=method,
        headers={
            "X-Consultant-Timestamp": timestamp,
            "X-Consultant-Signature": signature,
            "Content-Type": "application/json",
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run consultant-dashboard meeting reminders via the internal endpoint."
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_PATH),
        help="Path to the consultant-dashboard .env file.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Dashboard base URL, default %(default)s",
    )
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="Internal reminder endpoint path, default %(default)s",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds, default %(default)s",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress pretty-printed JSON response output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    secret = load_secret(Path(args.env_file))
    request = build_request(args.base_url, args.path, secret)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
            if not args.quiet:
                try:
                    parsed = json.loads(body)
                    print(json.dumps(parsed, indent=2, sort_keys=True))
                except json.JSONDecodeError:
                    print(body)
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(body or f"HTTP {exc.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
