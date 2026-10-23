"""WebSocket push server on /ws (same port as the web app).

Connect:  ws://HOST:PORT/ws?topics=status,events&cameras=camera_1&token=XXX
Topics:   status  periodic totals + per-camera counters        (websocket.status_hz)
          people  periodic people per camera, normalized boxes  (websocket.people_hz)
          events  crossing / ppe_alert / camera / reset, immediately
          tracks  one summary per person after they leave the frame
Client -> server JSON actions:
          {"action": "subscribe", "topics": [...]}     {"action": "unsubscribe", "topics": [...]}
          {"action": "set_cameras", "cameras": [...] | null}
          {"action": "get_status"}                      {"action": "ping"}
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from . import __version__

TOPICS = ("status", "people", "events", "tracks")
EVENT_TOPIC = {"crossing": "events", "ppe_alert": "events", "camera": "events", "reset": "events", "track": "tracks"}
SEND_TIMEOUT_S = 2.0
MAX_PENDING_EVENTS = 5000


@dataclass
class Client:
    id: int
    ws: WebSocket
    addr: str
    agent: str
    topics: set[str]
    cameras: set[str] | None = None
    connected_at: float = field(default_factory=time.time)
    sent: int = 0
    received: int = 0

    def wants_camera(self, camera_id: str | None) -> bool:
        return self.cameras is None or camera_id is None or camera_id in self.cameras

    def info(self) -> dict:
        return {
            "id": self.id,
            "addr": self.addr,
            "agent": self.agent,
            "topics": sorted(self.topics),
            "cameras": sorted(self.cameras) if self.cameras is not None else None,
            "connected_at": self.connected_at,
            "sent": self.sent,
            "received": self.received,
        }


def _dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _parse_list(raw: str | None) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


class WsHub:
    def __init__(self, manager, store) -> None:
        self.manager = manager
        self.store = store
        self.clients: dict[int, Client] = {}
        self.sent_by_topic: dict[str, int] = {t: 0 for t in TOPICS}
        self.dropped_events = 0
        self._next_id = 1
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._tasks: list[asyncio.Task] = []
        manager.hub.subscribe(self._on_event)

    def cfg(self) -> dict:
        return self.store.get("websocket") or {}

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._tasks = [
            asyncio.create_task(self._pump_events()),
            asyncio.create_task(self._ticker("status", "status_hz", self._status_payload)),
            asyncio.create_task(self._ticker("people", "people_hz", self._people_payload)),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for c in list(self.clients.values()):
            try:
                await c.ws.close(code=1001)
            except Exception:  # noqa: BLE001
                pass
        self.clients.clear()

    # ---------- events from worker threads ----------
    def _on_event(self, event: dict) -> None:
        loop, q = self._loop, self._queue
        if loop is None or q is None or not self.clients:
            return
        loop.call_soon_threadsafe(self._enqueue, event)

    def _enqueue(self, event: dict) -> None:
        if self._queue.qsize() >= MAX_PENDING_EVENTS:  # type: ignore[union-attr]
            self.dropped_events += 1
            return
        self._queue.put_nowait(event)  # type: ignore[union-attr]

    async def _pump_events(self) -> None:
        while True:
            ev = await self._queue.get()  # type: ignore[union-attr]
            topic = EVENT_TOPIC.get(ev.get("type"), "events")
            await self._broadcast(topic, ev, camera_id=ev.get("camera_id"))

    # ---------- periodic payloads ----------
    def _status_payload(self) -> dict:
        s = self.manager.status()
        cams = []
        for c in s["cameras"]:
            cr = c.get("crossing") or {}
            cams.append({
                "id": c["id"],
                "name": c.get("name"),
                "status": c.get("status"),
                "fps": c.get("fps"),
                "persons": c.get("persons", 0),
                "inside": cr.get("inside"),
                "outside": cr.get("outside"),
                "mode": cr.get("mode"),
                "entries": cr.get("entries", 0),
                "exits": cr.get("exits", 0),
                "ppe": c.get("ppe", {}),
                "ppe_alerts": c.get("ppe_alerts", 0),
            })
        ppe = s.get("ppe") or {}
        required = ppe.get("required", []) if ppe.get("enabled") else []
        return {"type": "status", "ts": time.time(), "totals": s["totals"], "ppe_required": required, "cameras": cams}

    def _people_payload(self) -> dict:
        cams = []
        for cam in self.store.cameras():
            w = self.manager.worker(cam["id"])
            if w is not None and w.status == "running":
                cams.append(w.people_detail())
        return {"type": "people", "ts": time.time(), "cameras": cams}

    async def _ticker(self, topic: str, hz_key: str, build) -> None:
        while True:
            cfg = self.cfg()
            hz = float(cfg.get(hz_key, 2.0) or 0)
            await asyncio.sleep(1.0 / hz if hz > 0 else 1.0)
            if not cfg.get("enabled", True):
                if self.clients:
                    await self._close_all(1013, "websocket disabled")
                continue
            if hz <= 0 or not any(topic in c.topics for c in self.clients.values()):
                continue
            try:
                payload = build()
            except Exception as exc:  # noqa: BLE001
                print(f"[ws] build {topic} failed: {exc}")
                continue
            await self._broadcast(topic, payload)

    # ---------- sending ----------
    @staticmethod
    def _filter_cameras(payload: dict, client: Client) -> dict:
        if client.cameras is None or "cameras" not in payload:
            return payload
        return {**payload, "cameras": [c for c in payload["cameras"] if c.get("id") in client.cameras]}

    async def _send(self, client: Client, text: str) -> bool:
        try:
            await asyncio.wait_for(client.ws.send_text(text), timeout=SEND_TIMEOUT_S)
            client.sent += 1
            return True
        except Exception:  # noqa: BLE001
            self.clients.pop(client.id, None)
            return False

    async def _broadcast(self, topic: str, payload: dict, *, camera_id: str | None = None) -> None:
        targets = [c for c in list(self.clients.values()) if topic in c.topics and c.wants_camera(camera_id)]
        if not targets:
            return
        plain = _dumps(payload)
        texts = [plain if c.cameras is None else _dumps(self._filter_cameras(payload, c)) for c in targets]
        results = await asyncio.gather(*(self._send(c, t) for c, t in zip(targets, texts)))
        self.sent_by_topic[topic] = self.sent_by_topic.get(topic, 0) + sum(results)

    async def _close_all(self, code: int, reason: str) -> None:
        for c in list(self.clients.values()):
            try:
                await c.ws.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001
                pass
        self.clients.clear()

    # ---------- connection ----------
    async def handle(self, ws: WebSocket) -> None:
        cfg = self.cfg()
        q = ws.query_params
        token = str(cfg.get("token") or "")
        await ws.accept()
        if not cfg.get("enabled", True):
            await ws.close(code=1013, reason="websocket disabled")
            return
        if token and q.get("token") != token:
            await ws.close(code=1008, reason="invalid token")
            return

        topics = {t for t in _parse_list(q.get("topics")) if t in TOPICS} or set(cfg.get("default_topics") or ["status"])
        cams = _parse_list(q.get("cameras"))
        client = Client(
            id=self._next_id,
            ws=ws,
            addr=f"{ws.client.host}:{ws.client.port}" if ws.client else "?",
            agent=ws.headers.get("user-agent", "")[:120],
            topics=topics,
            cameras=set(cams) if cams else None,
        )
        self._next_id += 1
        self.clients[client.id] = client

        await self._send(client, _dumps({
            "type": "hello",
            "client_id": client.id,
            "version": __version__,
            "server_time": time.time(),
            "topics": sorted(client.topics),
            "available_topics": list(TOPICS),
            "cameras": [{"id": c["id"], "name": c["name"], "enabled": c["enabled"]} for c in self.store.cameras()],
        }))
        if "status" in client.topics:
            await self._send(client, _dumps(self._filter_cameras(self._status_payload(), client)))

        try:
            while True:
                raw = await ws.receive_text()
                client.received += 1
                await self._handle_message(client, raw)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.clients.pop(client.id, None)

    async def _handle_message(self, client: Client, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(client, _dumps({"type": "error", "error": "invalid json"}))
            return
        action = msg.get("action")
        if action == "ping":
            reply = {"type": "pong", "ts": time.time()}
        elif action in ("subscribe", "unsubscribe"):
            topics = {t for t in msg.get("topics") or [] if t in TOPICS}
            client.topics = client.topics | topics if action == "subscribe" else client.topics - topics
            reply = {"type": "subscribed", "topics": sorted(client.topics)}
        elif action == "set_cameras":
            cams = msg.get("cameras")
            client.cameras = set(cams) if cams else None
            reply = {"type": "cameras_set", "cameras": sorted(client.cameras) if client.cameras else None}
        elif action == "get_status":
            reply = self._filter_cameras(self._status_payload(), client)
        else:
            reply = {"type": "error", "error": f"unknown action: {action}"}
        await self._send(client, _dumps(reply))

    def info(self) -> dict:
        return {
            "config": self.cfg(),
            "clients": [c.info() for c in self.clients.values()],
            "sent_by_topic": self.sent_by_topic,
            "dropped_events": self.dropped_events,
            "topics": list(TOPICS),
        }
