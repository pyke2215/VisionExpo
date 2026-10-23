"""In-process event hub: camera workers publish, database and WebSocket subscribe.

Event dicts always carry "type", "ts" and "camera_id". Types:
    crossing  track_id, direction ("in" | "out")
    ppe_alert track_id, items (NO-* labels), all_items (every PPE label on the person)
    camera    status ("online" | "offline" | "error" | "reconnecting")
    track     track_id, first_ts, last_ts, duration, frames, items {PPE label: frames}, alert (had a NO-*),
              first_side, last_side
    reset     counters reset for that camera
Subscribers are called from worker threads and must not block.
"""

from __future__ import annotations

import threading
from typing import Callable

Subscriber = Callable[[dict], None]


class EventHub:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs.append(fn)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001
                print(f"[events] subscriber failed: {exc}")
