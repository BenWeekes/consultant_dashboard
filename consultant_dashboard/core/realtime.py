import json
import threading
from collections import defaultdict
from queue import Queue

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
        q: Queue = Queue()
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
            queue.put(payload)


hub = _RealtimeHub()


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
            ws.send(json.dumps(queue.get()))
    except Exception:
        pass
    finally:
        hub.unsubscribe(client_id, queue)


@sock.route("/ws/client/messages/<token>")
def client_messages_ws(ws, token: str):
    db = get_db(current_app.config)
    link = get_client_access_link_by_hash(db, hash_access_token(token))
    db.close()
    if not link:
        ws.close()
        return

    client_id = link["client_id"]
    queue = hub.subscribe(client_id)
    try:
        ws.send(json.dumps({"type": "connected", "client_id": client_id}))
        while True:
            ws.send(json.dumps(queue.get()))
    except Exception:
        pass
    finally:
        hub.unsubscribe(client_id, queue)
