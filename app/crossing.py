"""People counting by line crossing or by zone (polygon) entry / exit, on the foot point of each track.

Config is normalized (0..1) so it survives resolution changes:
    {"enabled": true, "mode": "line" | "zone",
     "points": [{"x":..,"y":..}, {"x":..,"y":..}], "inward_point": {"x":..,"y":..},   # line mode
     "zone": [{"x":..,"y":..}, ...],                                                     # zone mode, >= 3 points
     "tasks": ["count", "ppe"]}                                                          # features for this geometry

line: a track whose foot point changes side of the segment A-B counts IN (towards inward_point) or OUT.
zone: a track whose foot point goes from outside to inside the polygon counts IN, inside to outside OUT;
      OUT events carry the time spent inside.
A track first seen already inside the zone is inside but not an entry (no double counts on ID switches).
`inside` / `outside` are the people currently visible on each side (line: the inward side counts as inside).

`tasks`: "count" emits crossing events; "ppe" (zone only) still updates `.side` so PPE can filter by inside.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

import cv2
import numpy as np

from .person import Person

SEGMENT_SLACK = 0.1  # accept crossings slightly beyond the segment ends (fraction of length)
DEAD_ZONE_FRAC = 0.006  # band around the line / zone border (fraction of frame diagonal) where side is undecided
COOLDOWN_S = 1.0  # ignore repeated flips of the same track within this window
STALE_TRACK_S = 30.0
MAX_ZONE_POINTS = 32
ALLOWED_TASKS = frozenset({"count", "ppe"})


def line_is_valid(cfg: dict | None) -> bool:
    return len((cfg or {}).get("points") or []) >= 2


def zone_is_valid(cfg: dict | None) -> bool:
    return len((cfg or {}).get("zone") or []) >= 3


def mode_of(cfg: dict | None) -> str:
    return "zone" if (cfg or {}).get("mode") == "zone" else "line"


def tasks_of(cfg: dict | None) -> list[str]:
    raw = (cfg or {}).get("tasks")
    if raw is None:
        return ["count"] if (cfg or {}).get("enabled", True) else []
    out: list[str] = []
    for t in raw:
        key = str(t).lower().strip()
        if key in ALLOWED_TASKS and key not in out:
            out.append(key)
    return out


class CrossingCounter:
    def __init__(self, cfg: dict | None) -> None:
        self._lock = threading.Lock()
        self.cfg: dict = {}
        self.entries = 0
        self.exits = 0
        self.inside = 0
        self.outside = 0
        self.events: deque[dict] = deque(maxlen=30)
        self._last_side: dict[int, int] = {}
        self._last_seen: dict[int, float] = {}
        self._last_event: dict[int, float] = {}
        self._entered_at: dict[int, float] = {}
        self.set_config(cfg)

    @property
    def mode(self) -> str:
        return mode_of(self.cfg)

    def tasks(self) -> list[str]:
        return tasks_of(self.cfg)

    def has_task(self, name: str) -> bool:
        return name in tasks_of(self.cfg)

    @property
    def geometry_ok(self) -> bool:
        return zone_is_valid(self.cfg) if self.mode == "zone" else line_is_valid(self.cfg)

    @property
    def enabled(self) -> bool:
        """Counting is on: task count + valid geometry."""
        return self.has_task("count") and self.geometry_ok

    @property
    def ppe_active(self) -> bool:
        """Zone has PPE task and a valid polygon (sides are maintained for inside filter)."""
        return self.mode == "zone" and self.has_task("ppe") and zone_is_valid(self.cfg)

    @property
    def active(self) -> bool:
        """Geometry is used for counting and/or PPE side tracking."""
        if not self.geometry_ok:
            return False
        return self.has_task("count") or (self.mode == "zone" and self.has_task("ppe"))

    def set_config(self, cfg: dict | None) -> None:
        with self._lock:
            key = lambda c: (mode_of(c), c.get("points"), c.get("inward_point"), c.get("zone"), tuple(tasks_of(c)))  # noqa: E731
            changed = key(self.cfg) != key(cfg or {})
            self.cfg = dict(cfg or {})
            if changed:
                self._clear_tracks()

    def _clear_tracks(self) -> None:
        self._last_side.clear()
        self._last_event.clear()
        self._entered_at.clear()
        self.inside = 0
        self.outside = 0

    def reset_counts(self) -> None:
        with self._lock:
            self.entries = 0
            self.exits = 0
            self.events.clear()
            self._clear_tracks()

    def line_px(self, w: int, h: int) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if not line_is_valid(self.cfg):
            return None
        a, b = self.cfg["points"][:2]
        return (float(a["x"]) * w, float(a["y"]) * h), (float(b["x"]) * w, float(b["y"]) * h)

    def zone_px(self, w: int, h: int) -> list[tuple[float, float]] | None:
        if not zone_is_valid(self.cfg):
            return None
        return [(float(p["x"]) * w, float(p["y"]) * h) for p in self.cfg["zone"][:MAX_ZONE_POINTS]]

    def _in_sign(self, a, b, w: int, h: int) -> int:
        inward = self.cfg.get("inward_point")
        if isinstance(inward, dict) and "x" in inward and "y" in inward:
            s = _cross(a, b, (float(inward["x"]) * w, float(inward["y"]) * h))
            if s != 0:
                return 1 if s > 0 else -1
        return 1

    def update(self, persons: list[Person], w: int, h: int, now: float | None = None) -> list[dict]:
        """Update sides, annotate persons with .side, return new crossing events (only if task count)."""
        now = time.time() if now is None else now
        with self._lock:
            if not self.active:
                self.inside = self.outside = 0
                for p in persons:
                    p.side = "unknown"
                return []
            dead = max(3.0, DEAD_ZONE_FRAC * math.hypot(w, h))
            if self.mode == "zone":
                events = self._update_zone(persons, w, h, now, dead)
            else:
                events = self._update_line(persons, w, h, now, dead)
            self.inside = sum(1 for p in persons if p.side == "in")
            self.outside = sum(1 for p in persons if p.side == "out")
            for ev in events:
                self.events.appendleft(ev)
            self._purge_stale(now)
            return events if self.has_task("count") else []

    def _count(self, tid: int, direction: str, now: float, extra: dict | None = None) -> dict:
        if direction == "in":
            self.entries += 1
        else:
            self.exits += 1
        self._last_event[tid] = now
        return {"ts": now, "track_id": tid, "direction": direction, "mode": self.mode, **(extra or {})}

    def _update_line(self, persons: list[Person], w: int, h: int, now: float, dead: float) -> list[dict]:
        events: list[dict] = []
        a, b = self.line_px(w, h)  # type: ignore[misc]
        ab_len = math.hypot(b[0] - a[0], b[1] - a[1])
        if ab_len < 1.0:
            return events
        in_sign = self._in_sign(a, b, w, h)
        do_count = self.has_task("count")
        for p in persons:
            fx, fy = p.foot_point()
            dist = _cross(a, b, (fx, fy)) / ab_len
            t = ((fx - a[0]) * (b[0] - a[0]) + (fy - a[1]) * (b[1] - a[1])) / (ab_len**2)
            side = 1 if dist > dead else -1 if dist < -dead else 0
            tid = p.track_id
            if tid is None:
                p.side = "unknown" if side == 0 else ("in" if side == in_sign else "out")
                continue
            self._last_seen[tid] = now
            prev = self._last_side.get(tid)
            if side != 0:
                within = -SEGMENT_SLACK <= t <= 1.0 + SEGMENT_SLACK
                cooled = now - self._last_event.get(tid, 0.0) >= COOLDOWN_S
                if do_count and prev is not None and prev != side and within and cooled:
                    events.append(self._count(tid, "in" if side == in_sign else "out", now))
                self._last_side[tid] = side
            cur = self._last_side.get(tid)
            p.side = "unknown" if cur is None else ("in" if cur == in_sign else "out")
        return events

    def _update_zone(self, persons: list[Person], w: int, h: int, now: float, dead: float) -> list[dict]:
        events: list[dict] = []
        poly = np.array(self.zone_px(w, h), dtype=np.float32).reshape(-1, 1, 2)
        do_count = self.has_task("count")
        for p in persons:
            fx, fy = p.foot_point()
            dist = cv2.pointPolygonTest(poly, (float(fx), float(fy)), True)  # > 0 inside
            side = 1 if dist > dead else -1 if dist < -dead else 0
            tid = p.track_id
            if tid is None:
                p.side = "unknown" if side == 0 else ("in" if side == 1 else "out")
                continue
            self._last_seen[tid] = now
            prev = self._last_side.get(tid)
            if side != 0:
                cooled = now - self._last_event.get(tid, 0.0) >= COOLDOWN_S
                if do_count and prev is not None and prev != side and cooled:
                    if side == 1:
                        self._entered_at[tid] = now
                        events.append(self._count(tid, "in", now))
                    else:
                        since = self._entered_at.pop(tid, None)
                        extra = {"dwell_s": round(now - since, 1)} if since is not None else None
                        events.append(self._count(tid, "out", now, extra))
                elif prev is None and side == 1:
                    self._entered_at.setdefault(tid, now)
                self._last_side[tid] = side
            cur = self._last_side.get(tid)
            p.side = "unknown" if cur is None else ("in" if cur == 1 else "out")
        return events

    def _purge_stale(self, now: float) -> None:
        if len(self._last_seen) <= 300:
            return
        for t in [t for t, ts in self._last_seen.items() if now - ts > STALE_TRACK_S]:
            self._last_seen.pop(t, None)
            self._last_side.pop(t, None)
            self._last_event.pop(t, None)
            self._entered_at.pop(t, None)

    def stats(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "ppe_active": self.ppe_active,
                "mode": self.mode,
                "tasks": self.tasks(),
                "inside": self.inside if self.active else None,
                "outside": self.outside if self.active else None,
                "entries": self.entries,
                "exits": self.exits,
                "events": list(self.events)[:10],
            }


def _cross(a, b, p) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
