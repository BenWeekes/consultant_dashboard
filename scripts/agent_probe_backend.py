#!/usr/bin/env python3
"""Helpers for starting and stopping synthetic agent probe sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import jwt


DEFAULT_BACKEND_BASE = "http://127.0.0.1:8082"
ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_agent_id(agent_response: Any) -> str:
    if not isinstance(agent_response, dict):
        return ""
    raw = agent_response.get("response")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return ""
        if isinstance(parsed, dict):
            return str(parsed.get("agent_id") or "")
    if isinstance(raw, dict):
        return str(raw.get("agent_id") or "")
    return ""


def _read_env_value(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    match = re.search(rf"^{re.escape(key)}=(.+)$", env_path.read_text(), re.M)
    return match.group(1).strip() if match else ""


def _default_probe_client_id() -> str:
    override = os.environ.get("AI_PROBE_CLIENT_ID", "").strip()
    if override:
        return override
    db_path = _read_env_value(ROOT_DIR / ".env", "CONSULTANT_DB_PATH")
    if not db_path:
        raise RuntimeError("CONSULTANT_DB_PATH not found in consultant_dashboard/.env")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id
            FROM clients
            WHERE is_active = 1
            ORDER BY created_at ASC, first_name ASC, last_name ASC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError("no active client found for probe auth")
    return str(row["id"])


def _mint_probe_auth_token(profile: str) -> str:
    simple_backend_env = ROOT_DIR.parent / "agent-samples" / "simple-backend" / ".env"
    secret = _read_env_value(simple_backend_env, f"{profile.upper()}_AUTH_JWT_SECRET")
    if not secret:
        raise RuntimeError(f"{profile.upper()}_AUTH_JWT_SECRET not found in {simple_backend_env}")
    client_id = _default_probe_client_id()
    user_id_hash = hashlib.sha256(f"client|{client_id}".encode("utf-8")).hexdigest()
    now = int(time.time())
    payload = {
        "user_id": user_id_hash,
        "client_id": client_id,
        "email": "",
        "name": "Synthetic Probe",
        "first_name": "Synthetic",
        "vendor_slug": os.environ.get("AI_PROBE_VENDOR_SLUG", "mindfix"),
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def start_probe(
    *,
    profile: str,
    backend_base: str,
    prompt: str = "",
    greeting: str = "",
    connect: bool = True,
) -> dict[str, Any]:
    query = {
        "profile": profile,
        "connect": "true" if connect else "false",
        "debug": "1",
    }
    # Empty greeting suppresses the normal automatic hello for cleaner probes.
    query["greeting"] = greeting
    if prompt:
        query["prompt"] = prompt
    url = f"{backend_base.rstrip('/')}/start-agent?{urllib.parse.urlencode(query)}"
    token = _mint_probe_auth_token(profile)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    agent_id = _parse_agent_id(payload.get("agent_response"))
    payload["agent_id"] = agent_id
    if not payload.get("channel") or not payload.get("appid") or not payload.get("token"):
        raise RuntimeError(f"start-agent returned incomplete payload: {payload}")
    if not agent_id:
        raise RuntimeError(f"start-agent did not return an agent_id: {payload.get('agent_response')}")
    return payload


def stop_probe(*, agent_id: str, backend_base: str, profile: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"agent_id": agent_id, "profile": profile})
    url = f"{backend_base.rstrip('/')}/hangup-agent?{query}"
    return _load_json(url)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--profile", default="therapy")
    start_parser.add_argument("--backend-base", default=DEFAULT_BACKEND_BASE)
    start_parser.add_argument("--prompt", default="")
    start_parser.add_argument("--greeting", default="")
    start_parser.add_argument("--token-only", action="store_true")

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--profile", default="therapy")
    stop_parser.add_argument("--backend-base", default=DEFAULT_BACKEND_BASE)
    stop_parser.add_argument("--agent-id", required=True)

    args = parser.parse_args()

    try:
        if args.command == "start":
            result = start_probe(
                profile=args.profile,
                backend_base=args.backend_base,
                prompt=args.prompt,
                greeting=args.greeting,
                connect=not args.token_only,
            )
        else:
            result = stop_probe(
                agent_id=args.agent_id,
                backend_base=args.backend_base,
                profile=args.profile,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
