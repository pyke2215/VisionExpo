"""People counting by line crossing, or occupancy / entry / exit per typed zone (polygons in app/zones.py).

Config is normalized (0..1) so it survives resolution changes:
    {"mode": "line" | "zone",
     "points": [{"x":..,"y":..}, {"x":..,"y":..}], "inward_point": {"x":..,"y":..},   # line mode
     "tasks": ["count"],                                                                 # line mode
     "zones": [{"id", "kind", "name", "points", "tasks", "rule", "overlap", ...}]}     # zone mode, see zones.py

line: a track whose foot point changes side of the segment A-B counts IN (towards inward_point) or OUT.
zone: every zone is judged by its own ZoneTracker (zones may nest). Person.zones = {zone_id: entered_at} for the
      zones a person is in now; Person.side follows the primary zone (the first "booth" zone, else the first
      zone), so the HUD, `inside` / `outside` and the person colours keep one meaning.
`inside` / `outside` are the people currently visible on each side of the line / primary zone.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from .person import Person
from .zones import DASHBOARD_KIND, MAX_ZONE_POINTS, ZoneTracker, dead_band, settled  # noqa: F401  (re-export)

SEGMENT_SLACK = 0.1  # accept crossings slightly beyond the segment ends (fraction of length)
DEAD_ZONE_FRAC = 0.006  # band around the line / zone border (fraction of frame diagonal) where side is undecided
COOLDOWN_S = 1.0  # ignore repeated flips of the same track within this window
STALE_TRACK_S = 30.0
LINE_KEY = "_line"  # count_state() key of the line counters


def line_is_valid(cfg: dict | None) -> bool:
    return len((cfg or {}).get("points") or []) >= 2


def mode_of(cfg: dict | None) -> str:
    return "zone" if (cfg or {}).get("mode") == "zone" else "line"


class CrossingCounter:
    def __init__(self, cfg: dict | None) -> None:
        self._lock = threading.Lock()
        self.cfg: dict = {}
        self.zones: list[ZoneTracker] = []
        self.line_entries = 0
        self.line_exits = 0
        self.inside = 0
        self.outside = 0
        self.events: deque[dict] = deque(maxlen=30)
        self._last_side: dict[int, int] = {}
        self._pending: dict[int, tuple[int, int]] = {}
        self._last_seen: dict[int, float] = {}
        self._last_event: dict[int, float] = {}
        self.set_config(cfg)

    # ---------- config ----------
    @property
    def mode(self) -> str:
        return mode_of(self.cfg)

    def tasks(self) -> list[str]:
        if self.mode == "zone":
            out: list[str] = []
            for z in self.zones:
                out += [t for t in z.cfg.get("tasks", []) if t not in out]
            return out
        return ["count"] if "count" in (self.cfg.get("tasks") or []) else []

    def has_task(self, name: str) -> bool:
        return name in self.tasks()

    @property
    def geometry_ok(self) -> bool:
        return bool(self.zones) if self.mode == "zone" else line_is_valid(self.cfg)

    @property
    def enabled(self) -> bool:
        """Counting is on: some geometry has the count task."""
        return self.has_task("count") and self.geometry_ok

    @property
    def ppe_active(self) -> bool:
        """At least one zone has the PPE task."""
        return bool(self.ppe_zones)

    @property
    def active(self) -> bool:
        """Geometry is in use: any zone (occupancy is kept even without tasks), or a counting line."""
        return bool(self.zones) if self.mode == "zone" else self.enabled

    @property
    def ppe_zones(self) -> list[ZoneTracker]:
        return [z for z in self.zones if z.has_task("ppe")] if self.mode == "zone" else []

    @property
    def dashboard_zones(self) -> list[ZoneTracker]:
        return [z for z in self.zones if z.kind == DASHBOARD_KIND] if self.mode == "zone" else []

    @property
    def primary(self) -> ZoneTracker | None:
        if self.mode != "zone" or not self.zones:
            return None
        return next((z for z in self.zones if z.kind == DASHBOARD_KIND), self.zones[0])

    def set_config(self, cfg: dict | None) -> None:
        cfg = dict(cfg or {})
        with self._lock:
            line_key = lambda c: (mode_of(c), c.get("points"), c.get("inward_point"))  # noqa: E731
            if line_key(self.cfg) != line_key(cfg):
                self._last_side.clear()
                self._pending.clear()
                self._last_event.clear()
            old = {z.id: z for z in self.zones}
            zones: list[ZoneTracker] = []
            for zc in (cfg.get("zones") or []) if mode_of(cfg) == "zone" else []:
                t = old.get(zc["id"])
                if t is None:
                    t = ZoneTracker(zc)
                else:
                    t.set_config(zc)  # keeps counts and per-track state unless the geometry changed
                zones.append(t)
            self.zones = zones
            self.cfg = cfg

    # ---------- counts ----------
    @property
    def entries(self) -> int:
        if self.mode == "zone":
            return sum(z.entries for z in self.zones if z.has_task("count"))
        return self.line_entries

    @property
    def exits(self) -> int:
        if self.mode == "zone":
            return sum(z.exits for z in self.zones if z.has_task("count"))
        return self.line_exits

    def count_state(self) -> dict:
        """{LINE_KEY | zone_id: (entries, exits)}: carried over when a worker restarts."""
        with self._lock:
            out = {LINE_KEY: (self.line_entries, self.line_exits)}
            out.update({z.id: (z.entries, z.exits) for z in self.zones})
            return out

    def restore_counts(self, counts: dict) -> None:
        """Inverse of count_state(); a None key (events saved before zones had ids) goes to the primary zone."""
        with self._lock:
            by_id = {z.id: z for z in self.zones}
            for key, (n_in, n_out) in (counts or {}).items():
                if key == LINE_KEY:
                    self.line_entries, self.line_exits = int(n_in), int(n_out)
                    continue
                z = by_id.get(key) if key is not None else (self.primary if self.mode == "zone" else None)
                if z is not None:
                    z.entries += int(n_in)
                    z.exits += int(n_out)
                elif key is None:
                    self.line_entries += int(n_in)
                    self.line_exits += int(n_out)

    def reset_counts(self) -> None:
        with self._lock:
            self.line_entries = 0
            self.line_exits = 0
            self.events.clear()
            self._last_side.clear()
            self._pending.clear()
            self._last_event.clear()
            for z in self.zones:
                z.reset_counts()
            self.inside = self.outside = 0

    # ---------- geometry ----------
    def line_px(self, w: int, h: int) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if not line_is_valid(self.cfg):
            return None
        a, b = self.cfg["points"][:2]
        return (float(a["x"]) * w, float(a["y"]) * h), (float(b["x"]) * w, float(b["y"]) * h)

    def zones_px(self, w: int, h: int) -> list[tuple[ZoneTracker, list[tuple[float, float]]]]:
        return [(z, z.polygon_px(w, h)) for z in self.zones] if self.mode == "zone" else []

    def _in_sign(self, a, b, w: int, h: int) -> int:
        inward = self.cfg.get("inward_point")
        if isinstance(inward, dict) and "x" in inward and "y" in inward:
            s = _cross(a, b, (float(inward["x"]) * w, float(inward["y"]) * h))
            if s != 0:
                return 1 if s > 0 else -1
        return 1

    # ---------- update ----------
    def update(self, persons: list[Person], w: int, h: int, now: float | None = None) -> list[dict]:
        """Update sides / zones on the persons, return new events (crossing only for count tasks, crowd)."""
        now = time.time() if now is None else now
        with self._lock:
            for p in persons:
                p.zones = {}
            if not self.active:
                self.inside = self.outside = 0
                for p in persons:
                    p.side = "unknown"
                return []
            dead = dead_band(w, h, DEAD_ZONE_FRAC)
            if self.mode == "zone":
                events = self._update_zones(persons, w, h, now, dead)
            else:
                events = self._update_line(persons, w, h, now, dead)
            self.inside = sum(1 for p in persons if p.side == "in")
            self.outside = sum(1 for p in persons if p.side == "out")
            for ev in events:
                if ev.get("type") == "crossing":
                    self.events.appendleft(ev)
            self._purge_stale(now)
            return events

    def _update_zones(self, persons: list[Person], w: int, h: int, now: float, dead: float) -> list[dict]:
        events: list[dict] = []
        primary = self.primary
        for z in self.zones:
            evs, sides = z.update(persons, w, h, now, dead)
            events += evs
            for p in persons:
                side = sides.get(id(p), "unknown")
                if side == "in":
                    tid = p.track_id
                    p.zones[z.id] = z.entered_at.get(tid, now) if tid is not None else now
                if z is primary:
                    p.side = side
        return events

    def _update_line(self, persons: list[Person], w: int, h: int, now: float, dead: float) -> list[dict]:
        events: list[dict] = []
        a, b = self.line_px(w, h)  # type: ignore[misc]
        ab_len = math.hypot(b[0] - a[0], b[1] - a[1])
        if ab_len < 1.0:
            return events
        in_sign = self._in_sign(a, b, w, h)
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
            if side != 0 and not settled(self._pending, tid, side, prev):
                side = 0  # not confirmed yet: keep the previous side this frame
            if side != 0:
                within = -SEGMENT_SLACK <= t <= 1.0 + SEGMENT_SLACK
                cooled = now - self._last_event.get(tid, 0.0) >= COOLDOWN_S
                if prev is not None and prev != side and within and cooled:
                    direction = "in" if side == in_sign else "out"
                    if direction == "in":
                        self.line_entries += 1
                    else:
                        self.line_exits += 1
                    self._last_event[tid] = now
                    events.append({"type": "crossing", "ts": now, "track_id": tid, "direction": direction,
                                   "mode": "line"})
                self._last_side[tid] = side
            cur = self._last_side.get(tid)
            p.side = "unknown" if cur is None else ("in" if cur == in_sign else "out")
        return events

    def _purge_stale(self, now: float) -> None:
        if len(self._last_seen) <= 300:
            return
        for t in [t for t, ts in self._last_seen.items() if now - ts > STALE_TRACK_S]:
            for d in (self._last_seen, self._last_side, self._pending, self._last_event):
                d.pop(t, None)

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            primary = self.primary
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
                "primary_zone": primary.id if primary else None,
                "zones": [z.stats(now) for z in self.zones],
            }


def _cross(a, b, p) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
