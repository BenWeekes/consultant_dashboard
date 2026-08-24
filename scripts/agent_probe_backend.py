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
from dotenv import dotenv_values


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
    override = (
        os.environ.get("AI_PROBE_CLIENT_ID", "").strip()
        or _read_env_value(ROOT_DIR / ".env", "AI_PROBE_CLIENT_ID")
    )
    if override:
        return override
    raise RuntimeError(
        "AI_PROBE_CLIENT_ID must identify a dedicated synthetic client; "
        "using a real client would contaminate therapy history"
    )


def _load_probe_client(client_id: str) -> sqlite3.Row:
    db_path = _read_env_value(ROOT_DIR / ".env", "CONSULTANT_DB_PATH")
    if not db_path:
        raise RuntimeError("CONSULTANT_DB_PATH not found in consultant_dashboard/.env")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT c.id, c.first_name, c.last_name, c.email, c.phone_number,
                   c.ai_escalation_enabled, co.ai_testing_mode, v.slug AS vendor_slug
            FROM clients c
            JOIN consultant_clients cc ON cc.client_id = c.id
            JOIN consultants co ON co.id = cc.consultant_id
            JOIN vendors v ON v.id = c.vendor_id
            WHERE c.id = ? AND c.is_active = 1
            """,
            (client_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError("AI_PROBE_CLIENT_ID does not identify an active client")
    if not row["ai_testing_mode"] or row["ai_escalation_enabled"]:
        raise RuntimeError(
            "probe client must belong to a testing consultant and have AI escalation disabled"
        )
    if not row["email"] or not row["phone_number"]:
        raise RuntimeError("probe client requires synthetic email and phone identity fields")
    return row


def _ensure_backend_probe_profile(profile: str, client: sqlite3.Row, user_id_hash: str) -> None:
    backend_root = ROOT_DIR.parent / "agent-samples" / "simple-backend"
    for key, value in dotenv_values(backend_root / ".env").items():
        if value is not None:
            os.environ.setdefault(key, value)
    sys.path.insert(0, str(backend_root))
    from core.auth import _save_dashboard_profile
    from core.config import initialize_constants

    constants = initialize_constants(profile)
    auth_data_dir = Path(constants.get("AUTH_DATA_DIR") or "./data")
    if not auth_data_dir.is_absolute():
        constants["AUTH_DATA_DIR"] = str((backend_root / auth_data_dir).resolve())
    saved_hash = _save_dashboard_profile(
        constants,
        str(client["id"]),
        str(client["email"]),
        f"{client['first_name']} {client['last_name']}".strip(),
        str(client["phone_number"]),
        first_name=str(client["first_name"]),
    )
    if saved_hash != user_id_hash:
        raise RuntimeError("synthetic probe profile hash did not match JWT identity")


def _mint_probe_auth_token(profile: str) -> str:
    simple_backend_env = ROOT_DIR.parent / "agent-samples" / "simple-backend" / ".env"
    secret = _read_env_value(simple_backend_env, f"{profile.upper()}_AUTH_JWT_SECRET")
    if not secret:
        raise RuntimeError(f"{profile.upper()}_AUTH_JWT_SECRET not found in {simple_backend_env}")
    client_id = _default_probe_client_id()
    user_id_hash = hashlib.sha256(f"client|{client_id}".encode("utf-8")).hexdigest()
    client = _load_probe_client(client_id)
    _ensure_backend_probe_profile(profile, client, user_id_hash)
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
    include_debug: bool = False,
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
    payload["probe_auth_token"] = token
    llm_config = (
        payload.get("debug", {})
        .get("agent_payload", {})
        .get("properties", {})
        .get("llm", {})
    )
    if isinstance(llm_config, dict):
        payload["llm_has_api_key"] = "api_key" in llm_config
        payload["llm_auth_configured"] = bool(llm_config.get("api_key"))
        payload["llm_vendor"] = llm_config.get("vendor")
        payload["llm_url"] = llm_config.get("url")
        payload["llm_model"] = llm_config.get("params", {}).get("model")
        payload["llm_reasoning_effort"] = llm_config.get("params", {}).get("reasoning_effort")
    if not include_debug:
        payload.pop("debug", None)
    if not payload.get("channel") or not payload.get("appid") or not payload.get("token"):
        raise RuntimeError(f"start-agent returned incomplete payload: {payload}")
    if not agent_id:
        raise RuntimeError(f"start-agent did not return an agent_id: {payload.get('agent_response')}")
    return payload


def stop_probe(
    *, agent_id: str, backend_base: str, profile: str, channel: str = "", auth_token: str = ""
) -> dict[str, Any]:
    params = {"agent_id": agent_id, "profile": profile}
    if channel:
        params["channel"] = channel
    query = urllib.parse.urlencode(params)
    url = f"{backend_base.rstrip('/')}/hangup-agent?{query}"
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--profile", default="therapy")
    start_parser.add_argument("--backend-base", default=DEFAULT_BACKEND_BASE)
    start_parser.add_argument("--prompt", default="")
    start_parser.add_argument("--greeting", default="")
    start_parser.add_argument("--token-only", action="store_true")
    start_parser.add_argument("--include-debug", action="store_true")

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--profile", default="therapy")
    stop_parser.add_argument("--backend-base", default=DEFAULT_BACKEND_BASE)
    stop_parser.add_argument("--agent-id", required=True)
    stop_parser.add_argument("--channel", default="")
    stop_parser.add_argument("--auth-token", default="")

    args = parser.parse_args()

    try:
        if args.command == "start":
            result = start_probe(
                profile=args.profile,
                backend_base=args.backend_base,
                prompt=args.prompt,
                greeting=args.greeting,
                connect=not args.token_only,
                include_debug=args.include_debug,
            )
        else:
            result = stop_probe(
                agent_id=args.agent_id,
                backend_base=args.backend_base,
                profile=args.profile,
                channel=args.channel,
                auth_token=args.auth_token,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
