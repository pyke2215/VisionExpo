"""Typed zones of one camera: polygons with their own tasks, entry / exit, dwell time and crowding.

A camera in zone mode has a list of zones (`crossing.zones`); each is judged on its own, so zones may nest
(a "3D Box Game" corner inside the "Booth area") and one person can be in several at once.

Zone config (normalized 0..1 coordinates):
    {"id": "z1", "kind": "booth", "name": "Booth area", "points": [{"x":..,"y":..}, ...],
     "tasks": ["count", "ppe"],            # count = entry / exit events, ppe = hardhat / gloves check; may be empty
     "rule": "body" | "box" | "foot", "overlap": 0.5,
     "crowd_max": 0, "crowd_s": 10}         # crowd event when more than crowd_max people stay crowd_s seconds

Occupancy (`inside`) and dwell time are kept for every zone, task or not.
rule: "body" = inside when >= overlap of the lower part of the box lies in the polygon (seated people, feet cut
off by the frame); "box" = the same share over the whole box (zone drawn over the whole scene); "foot" = the
foot point is inside.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:  # person.py loads ultralytics; settings_store imports this module without it
    from .person import Person

ZONE_KINDS = {  # kind -> default display name; the order is the order used in the UI
    "booth": "Booth area",
    "game_3d": "3D Box Game",
    "monitoring": "Camera Monitoring",
    "reception": "Reception",
    "other": "Khu vực khác",
}
ZONE_COLORS = {  # kind -> "#rrggbb", shared by the video overlay, the web pages and the 3D dashboard
    "booth": "#c800ff",
    "game_3d": "#28a0ff",
    "monitoring": "#ffc828",
    "reception": "#50dc78",
    "other": "#c8c8c8",
}
DASHBOARD_KIND = "booth"  # people inside a zone of this kind are sent to the 3D show dashboard
ZONE_TASKS = ("count", "ppe")
ZONE_RULES = ("body", "box", "foot")
MAX_ZONE_POINTS = 32
MAX_ZONES = 12

SIDE_CONFIRM_FRAMES = 3  # a new side must hold this many frames in a row (keypoint jitter on a turn)
COOLDOWN_S = 1.0  # ignore repeated flips of the same track within this window
BODY_FRAC = 0.4  # body rule: bottom share of the box that is judged (legs / lap; the head sits above a floor zone)
OVERLAP_HYST = 0.05  # body rule: in above overlap + this, out below overlap - this, undecided between
CROWD_END_S = 5.0  # a crowd ends only after the count stays at / under the limit this long (no flicker)
STALE_TRACK_S = 30.0


# ---------- config ----------
def _norm_point(p) -> dict:
    return {"x": float(min(max(float(p["x"]), 0.0), 1.0)), "y": float(min(max(float(p["y"]), 0.0), 1.0))}


def normalize_zone(z: dict, index: int, used_ids: set[str]) -> dict:
    """Valid zone dict with defaults; raises ValueError on a malformed one (API turns it into a 400)."""
    kind = str(z.get("kind") or "other")
    if kind not in ZONE_KINDS:
        raise ValueError(f"unknown zone kind '{kind}'; allowed: {list(ZONE_KINDS)}")
    pts = z.get("points") or []
    if not isinstance(pts, list) or not 3 <= len(pts) <= MAX_ZONE_POINTS:
        raise ValueError(f"zone '{z.get('name') or kind}' needs 3..{MAX_ZONE_POINTS} points")
    tasks: list[str] = []
    for t in z.get("tasks") or []:
        key = str(t).lower().strip()
        if key not in ZONE_TASKS:
            raise ValueError(f"unknown task '{t}'; allowed: {list(ZONE_TASKS)}")
        if key not in tasks:
            tasks.append(key)
    rule = z.get("rule") if z.get("rule") in ZONE_RULES else "body"
    zid = re.sub(r"[^a-zA-Z0-9_-]+", "", str(z.get("id") or ""))[:24]
    if not zid or zid in used_ids:
        n = index + 1
        while f"z{n}" in used_ids:
            n += 1
        zid = f"z{n}"
    used_ids.add(zid)
    return {
        "id": zid,
        "kind": kind,
        "name": (str(z.get("name") or "").strip() or ZONE_KINDS[kind])[:40],
        "points": [_norm_point(p) for p in pts],
        "tasks": tasks,
        "rule": rule,
        "overlap": float(min(max(float(z.get("overlap", 0.5)), 0.1), 0.9)),
        "crowd_max": int(min(max(int(z.get("crowd_max") or 0), 0), 100)),
        "crowd_s": float(min(max(float(z.get("crowd_s") or 10), 1.0), 600.0)),
    }


def normalize_zones(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("zones must be a list")
    if len(raw) > MAX_ZONES:
        raise ValueError(f"at most {MAX_ZONES} zones per camera")
    used: set[str] = set()
    return [normalize_zone(z, i, used) for i, z in enumerate(raw)]


def migrate_single_zone(cr: dict) -> list[dict]:
    """Old one-polygon config (zone + tasks + zone_rule + zone_overlap) -> one "Booth area" zone."""
    pts = cr.get("zone") or []
    if len(pts) < 3:
        return []
    tasks = cr.get("tasks")
    if tasks is None:
        tasks = ["count"] if cr.get("enabled", True) else []
    return normalize_zones([{
        "id": "z1", "kind": DASHBOARD_KIND, "name": ZONE_KINDS[DASHBOARD_KIND], "points": pts,
        "tasks": [t for t in tasks if t in ZONE_TASKS],
        "rule": cr.get("zone_rule", "body"), "overlap": cr.get("zone_overlap", 0.5),
    }])


def geometry_key(z: dict) -> tuple:
    """What a tracker's per-track state depends on: changing it starts the zone afresh."""
    return (tuple((p["x"], p["y"]) for p in z.get("points") or []), z.get("rule"), z.get("overlap"))


# ---------- runtime ----------
def settled(pending: dict[int, tuple[int, int]], tid: int, side: int, prev: int | None) -> bool:
    """True when `side` may replace `prev`: same side, first reading, or held SIDE_CONFIRM_FRAMES frames."""
    if prev is None or side == prev:
        pending.pop(tid, None)
        return True
    cand, n = pending.get(tid, (side, 0))
    n = n + 1 if cand == side else 1
    if n >= SIDE_CONFIRM_FRAMES:
        pending.pop(tid, None)
        return True
    pending[tid] = (side, n)
    return False


class ZoneTracker:
    """One zone of one camera. Not thread-safe on its own: CrossingCounter holds the lock."""

    def __init__(self, cfg: dict) -> None:
        self.cfg: dict = {}
        self.entries = 0
        self.exits = 0
        self.inside = 0  # people in the zone this frame (tracked or not)
        self.inside_ids: set[int] = set()
        self.entered_at: dict[int, float] = {}  # tid -> time it was first in the zone (this visit)
        self._last_side: dict[int, int] = {}
        self._pending: dict[int, tuple[int, int]] = {}
        self._last_event: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}
        self._integral: tuple[tuple, np.ndarray] | None = None
        self.crowded = False
        self.crowd_since: float | None = None
        self._over_since: float | None = None
        self._under_since: float | None = None
        self.crowd_peak = 0
        self.set_config(cfg)

    def set_config(self, cfg: dict) -> None:
        if geometry_key(cfg) != geometry_key(self.cfg):
            self._clear_tracks()
        self.cfg = dict(cfg)
        if not self.cfg.get("crowd_max") and self.crowded:
            self.crowded = False
            self.crowd_since = None

    def _clear_tracks(self) -> None:
        self._last_side.clear()
        self._pending.clear()
        self._last_event.clear()
        self.entered_at.clear()
        self.inside_ids.clear()
        self.inside = 0

    def reset_counts(self) -> None:
        self.entries = 0
        self.exits = 0
        self._clear_tracks()

    # ----- properties -----
    @property
    def id(self) -> str:
        return self.cfg["id"]

    @property
    def kind(self) -> str:
        return self.cfg["kind"]

    @property
    def name(self) -> str:
        return self.cfg["name"]

    def has_task(self, task: str) -> bool:
        return task in self.cfg.get("tasks", [])

    def polygon_px(self, w: int, h: int) -> list[tuple[float, float]]:
        return [(float(p["x"]) * w, float(p["y"]) * h) for p in self.cfg["points"][:MAX_ZONE_POINTS]]

    def contains(self, x: float, y: float, w: int, h: int) -> bool:
        poly = np.array(self.polygon_px(w, h), dtype=np.float32).reshape(-1, 1, 2)
        return cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0

    # ----- membership -----
    def _integral_img(self, w: int, h: int) -> np.ndarray:
        """Summed-area table of the filled polygon, so any box's overlap is 4 lookups (rebuilt on size / zone change)."""
        key = (w, h, geometry_key(self.cfg)[0])
        if self._integral is None or self._integral[0] != key:
            mask = np.zeros((h, w), np.uint8)
            cv2.fillPoly(mask, [np.array(self.polygon_px(w, h), np.float32).round().astype(np.int32)], 1)
            self._integral = (key, cv2.integral(mask))
        return self._integral[1]

    def _body_side(self, p: Person, w: int, h: int, prev: int | None) -> int:
        """1 / -1 / 0 from the share of the judged box part inside the zone.

        Once a track has a side, a hysteresis band around `overlap` returns 0 (keep it); a first reading decides
        on the threshold alone, so someone standing in the band still gets a side.
        """
        x1, y1, x2, y2 = p.box
        x1, x2 = int(max(0, min(w, x1))), int(max(0, min(w, x2)))
        y2i = int(max(0, min(h, y2)))
        frac = 1.0 if self.cfg["rule"] == "box" else BODY_FRAC
        y1i = int(max(0, min(h, y2 - frac * (y2 - y1))))
        area = (x2 - x1) * (y2i - y1i)
        if area <= 0:
            return 0
        s = self._integral_img(w, h)
        share = float(s[y2i, x2] - s[y1i, x2] - s[y2i, x1] + s[y1i, x1]) / area
        thr = float(self.cfg["overlap"])
        if prev is None:
            return 1 if share >= thr else -1
        return 1 if share >= thr + OVERLAP_HYST else -1 if share <= thr - OVERLAP_HYST else 0

    def _raw_side(self, p: Person, w: int, h: int, dead: float) -> int:
        if self.cfg["rule"] != "foot":
            return self._body_side(p, w, h, self._last_side.get(p.track_id) if p.track_id is not None else None)
        fx, fy = p.foot_point()
        poly = np.array(self.polygon_px(w, h), dtype=np.float32).reshape(-1, 1, 2)
        dist = cv2.pointPolygonTest(poly, (float(fx), float(fy)), True)  # > 0 inside
        return 1 if dist > dead else -1 if dist < -dead else 0

    def update(self, persons: list[Person], w: int, h: int, now: float, dead: float) -> tuple[list[dict], dict]:
        """Judge every person; return (events, {id(person): "in" | "out" | "unknown"})."""
        events: list[dict] = []
        sides: dict[int, str] = {}
        do_count = self.has_task("count")
        inside_ids: set[int] = set()
        inside = 0
        for p in persons:
            side = self._raw_side(p, w, h, dead)
            tid = p.track_id
            if tid is None:
                sides[id(p)] = "unknown" if side == 0 else ("in" if side == 1 else "out")
                inside += side == 1
                continue
            self._last_seen[tid] = now
            prev = self._last_side.get(tid)
            if side != 0 and not settled(self._pending, tid, side, prev):
                side = 0  # not confirmed yet: keep the previous side this frame
            if side != 0:
                if prev is not None and prev != side:
                    cooled = now - self._last_event.get(tid, 0.0) >= COOLDOWN_S
                    if side == 1:
                        self.entered_at[tid] = now
                        if do_count and cooled:
                            events.append(self._count(tid, "in", now))
                    else:
                        since = self.entered_at.pop(tid, None)
                        if do_count and cooled:
                            extra = {"dwell_s": round(now - since, 1)} if since is not None else None
                            events.append(self._count(tid, "out", now, extra))
                elif prev is None and side == 1:
                    self.entered_at.setdefault(tid, now)  # first seen already inside: inside, not an entry
                self._last_side[tid] = side
            cur = self._last_side.get(tid)
            sides[id(p)] = "unknown" if cur is None else ("in" if cur == 1 else "out")
            if cur == 1:
                inside_ids.add(tid)
                inside += 1
        self.inside_ids = inside_ids
        self.inside = inside
        events += self._crowd(now)
        self._purge_stale(now)
        return events, sides

    def _count(self, tid: int, direction: str, now: float, extra: dict | None = None) -> dict:
        if direction == "in":
            self.entries += 1
        else:
            self.exits += 1
        self._last_event[tid] = now
        return {"type": "crossing", "ts": now, "track_id": tid, "direction": direction, "mode": "zone",
                **self.ref(), **(extra or {})}

    def _crowd(self, now: float) -> list[dict]:
        """crowd start when > crowd_max for crowd_s; end after CROWD_END_S at / under the limit."""
        limit = int(self.cfg.get("crowd_max") or 0)
        if limit <= 0:
            self._over_since = self._under_since = None
            return []
        if self.inside > limit:
            self._under_since = None
            self.crowd_peak = max(self.crowd_peak, self.inside) if self.crowded else self.inside
            if self._over_since is None:
                self._over_since = now
            if not self.crowded and now - self._over_since >= float(self.cfg.get("crowd_s", 10)):
                self.crowded = True
                self.crowd_since = self._over_since
                return [{"type": "crowd", "ts": now, "state": "start", "inside": self.inside, "max": limit,
                         **self.ref()}]
            return []
        self._over_since = None
        if not self.crowded:
            return []
        if self._under_since is None:
            self._under_since = now
        if now - self._under_since < CROWD_END_S:
            return []
        started = self.crowd_since or now
        self.crowded = False
        self.crowd_since = self._under_since = None
        return [{"type": "crowd", "ts": now, "state": "end", "inside": self.inside, "max": limit,
                 "peak": self.crowd_peak, "duration_s": round(now - started, 1), **self.ref()}]

    def _purge_stale(self, now: float) -> None:
        if len(self._last_seen) <= 300:
            return
        for t in [t for t, ts in self._last_seen.items() if now - ts > STALE_TRACK_S]:
            for d in (self._last_seen, self._last_side, self._pending, self._last_event, self.entered_at):
                d.pop(t, None)

    # ----- output -----
    def ref(self) -> dict:
        """Fields that tag an event / sample with this zone."""
        return {"zone_id": self.id, "zone_kind": self.kind, "zone_name": self.name}

    def stats(self, now: float) -> dict:
        dwell = [now - self.entered_at[t] for t in self.inside_ids if t in self.entered_at]
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "color": ZONE_COLORS.get(self.kind, ZONE_COLORS["other"]),
            "points": self.cfg["points"],
            "tasks": list(self.cfg.get("tasks", [])),
            "inside": self.inside,
            "entries": self.entries,
            "exits": self.exits,
            "dwell_avg_s": round(sum(dwell) / len(dwell), 1) if dwell else None,
            "dwell_max_s": round(max(dwell), 1) if dwell else None,
            "crowd_max": int(self.cfg.get("crowd_max") or 0),
            "crowded": self.crowded,
            "crowd_since": self.crowd_since,
        }


def dead_band(w: int, h: int, frac: float) -> float:
    """Band around a line / zone border (pixels) where the foot point decides nothing."""
    return max(3.0, frac * math.hypot(w, h))
