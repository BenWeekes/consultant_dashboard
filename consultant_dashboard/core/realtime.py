import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from queue import Empty, Full, Queue

from flask import current_app, session
from flask_sock import Sock

from .db import get_client_access_link_by_hash, get_client_detail, get_db
from .messaging import hash_access_token

sock = Sock()


class _RealtimeHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = defaultdict(list)

    def subscribe(self, client_id: str) -> Queue:
        q: Queue = Queue(maxsize=100)
        with self._lock:
            self._subscribers[client_id].append(q)
        return q

    def unsubscribe(self, client_id: str, queue: Queue) -> None:
        with self._lock:
            queues = self._subscribers.get(client_id, [])
            if queue in queues:
                queues.remove(queue)
            if not queues and client_id in self._subscribers:
                self._subscribers.pop(client_id, None)

    def publish(self, client_id: str, payload: dict) -> None:
        with self._lock:
            queues = list(self._subscribers.get(client_id, []))
        for queue in queues:
            try:
                queue.put_nowait(payload)
            except Full:
                # Drop the oldest notification rather than allowing a slow
                # browser to grow process memory without bound.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (Empty, Full):
                    pass


hub = _RealtimeHub()


def _parse_iso_datetime(value: str):
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_active_client_link(config: dict, token: str):
    db = get_db(config)
    try:
        link = get_client_access_link_by_hash(db, hash_access_token(token))
    finally:
        db.close()
    if not link:
        return None
    expires_at = _parse_iso_datetime(link["expires_at"])
    if expires_at and expires_at < datetime.now(timezone.utc):
        return None
    return link


def publish_client_thread_update(client_id: str) -> None:
    hub.publish(client_id, {"type": "thread_updated", "client_id": client_id})


def configure_realtime(app):
    sock.init_app(app)


@sock.route("/ws/consultant/clients/<client_id>/messages")
def consultant_messages_ws(ws, client_id: str):
    consultant_id = session.get("consultant_id")
    if not consultant_id:
        ws.close()
        return
    db = get_db(current_app.config)
    client = get_client_detail(db, client_id, consultant_id=consultant_id)
    db.close()
    if not client:
        ws.close()
        return

    queue = hub.subscribe(client_id)
    try:
        ws.send(json.dumps({"type": "connected", "client_id": client_id}))
        while True:
            try:
                payload = queue.get(timeout=20)
                ws.send(json.dumps(payload))
            except Empty:
                ws.send(json.dumps({"type": "heartbeat"}))
    except Exception:
        pass
    finally:
        hub.unsubscribe(client_id, queue)


@sock.route("/ws/client/messages/<token>")
def client_messages_ws(ws, token: str):
    link = get_active_client_link(current_app.config, token)
    if not link:
        ws.close()
        return

    client_id = link["client_id"]
    queue = hub.subscribe(client_id)
    try:
        ws.send(json.dumps({"type": "connected", "client_id": client_id}))
        while True:
            try:
                payload = queue.get(timeout=20)
                ws.send(json.dumps(payload))
            except Empty:
                ws.send(json.dumps({"type": "heartbeat"}))
    except Exception:
        pass
    finally:
        hub.unsubscribe(client_id, queue)
