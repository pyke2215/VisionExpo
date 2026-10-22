"""One thread per camera: latest frame, person tracking, crossing count, object detection, overlay, JPEG."""

from __future__ import annotations

import threading
import time
from collections import Counter

import cv2
import numpy as np

from .capture import FrameReader
from .crossing import CrossingCounter
from .draw import attach_objects, render
from .events import EventHub
from .items import ItemTracker
from .person import Person, PersonTracker
from .ppe import PpeFinding, PpeService, expand_box
from .world import WorldObject, WorldService

TRACK_END_S = 3.0  # a track unseen this long is considered gone and summarized
MIN_TRACK_FRAMES = 5  # shorter tracks are flicker, not visitors
MIN_ITEM_FRAMES = 3  # an item must be shown this many frames to count in the summary
ALERT_REPEAT_S = 30.0
PPE_ALERT_REPEAT_S = 8.0
STATE_OF_STATUS = {"running": "online", "offline": "offline", "error": "error", "reconnecting": "reconnecting"}


def _placeholder(text: str, sub: str = "", size: tuple[int, int] = (1280, 720)) -> np.ndarray:
    img = np.full((size[1], size[0], 3), 32, np.uint8)
    cv2.putText(img, text, (40, size[1] // 2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (230, 230, 230), 3, cv2.LINE_AA)
    if sub:
        cv2.putText(img, sub[:90], (40, size[1] // 2 + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (150, 150, 150), 2, cv2.LINE_AA)
    return img


class CameraWorker:
    def __init__(
        self,
        cfg: dict,
        *,
        person_weights,
        device: str,
        world: WorldService,
        ppe: PpeService | None = None,
        hub: EventHub | None = None,
        initial_counts: tuple[int, int] = (0, 0),
    ) -> None:
        self.cfg = dict(cfg)
        self.id = cfg["id"]
        self.person_weights = person_weights
        self.device = device
        self.world = world
        self.ppe_svc = ppe
        self.hub = hub
        self.crossing = CrossingCounter(cfg.get("crossing"))
        self.crossing.entries, self.crossing.exits = initial_counts
        self._tracks: dict[int, dict] = {}
        self._alerted: dict[int, float] = {}
        self._ppe_alerted: dict[tuple[int, str], float] = {}
        self._cam_state: str | None = None
        self.reader = FrameReader(cfg["source"])
        self.tracker: PersonTracker | None = None
        self.status = "starting"
        self.error: str | None = None

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"worker-{self.id}", daemon=True)
        self._jpeg_lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._jpeg_seq = 0

        self.fps = 0.0
        self.infer_ms = 0.0
        self.persons: list[Person] = []
        self.objects: list[WorldObject] = []
        self.ppe_findings: list[PpeFinding] = []
        self.items = ItemTracker()
        self._world_tick = 0
        self._ppe_tick = 0

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.reader.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.reader.stop()
        self._thread.join(timeout=3.0)
        for tid in list(self._tracks):
            self._finish_track(tid)

    def apply_config(self, cfg: dict) -> None:
        """Hot update for fields that don't need a new capture/model."""
        self.cfg = dict(cfg)
        self.crossing.set_config(cfg.get("crossing"))

    # ---------- outputs ----------
    def latest_jpeg(self) -> tuple[bytes | None, int]:
        with self._jpeg_lock:
            return self._jpeg, self._jpeg_seq

    def raw_jpeg(self, quality: int = 90) -> bytes | None:
        frame, _ = self.reader.latest()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def stats(self) -> dict:
        persons = self.persons
        objects = self.objects
        return {
            "id": self.id,
            "name": self.cfg.get("name", self.id),
            "status": self.status,
            "reader": self.reader.status,
            "error": self.error or self.reader.error,
            "frame_size": self.reader.frame_size,
            "fps": round(self.fps, 1),
            "infer_ms": round(self.infer_ms, 1),
            "persons": len(persons),
            "people": [
                {
                    "track_id": p.track_id,
                    "side": p.side,
                    "items": sorted(set(p.items)),
                    "alert": p.alert,
                    "ppe_alert": p.ppe_alert,
                }
                for p in persons
            ],
            "objects": dict(Counter(o.label for o in objects)),
            "ppe": dict(Counter(f.label for f in self.ppe_findings)),
            "alerts": sum(1 for p in persons if p.alert),
            "ppe_alerts": sum(1 for p in persons if p.ppe_alert),
            "crossing": self.crossing.stats(),
        }

    def people_detail(self) -> dict:
        """People with boxes / foot points normalized to 0..1 (for WebSocket clients)."""
        size = self.reader.frame_size or (1, 1)
        w, h = float(size[0]), float(size[1])
        norm = lambda b: [round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)]  # noqa: E731
        objs_by_owner: dict[int, list[dict]] = {}
        for o in self.objects:
            if o.owner is None:
                continue
            objs_by_owner.setdefault(o.owner, []).append({
                "label": o.label,
                "vi": self.world.vi.get(o.label.lower(), o.label),
                "category": o.category or self.world.categories.get(o.label.lower(), ""),
                "conf": round(o.conf, 3),
                "alert": o.alert,
                "box": norm(o.box),
            })
        ppe_by_owner: dict[int, list[dict]] = {}
        for f in self.ppe_findings:
            if f.owner is None:
                continue
            ppe_by_owner.setdefault(f.owner, []).append({
                "label": f.label,
                "kind": f.kind,
                "conf": round(f.conf, 3),
                "alert": f.alert,
                "box": norm(f.box),
            })
        people = []
        for p in self.persons:
            fx, fy = p.foot_point()
            people.append({
                "track_id": p.track_id,
                "side": p.side,
                "alert": p.alert,
                "ppe_alert": p.ppe_alert,
                "items": sorted(set(p.items)),
                "objects": objs_by_owner.get(p.track_id, []) if p.track_id is not None else [],
                "ppe": ppe_by_owner.get(p.track_id, []) if p.track_id is not None else [],
                "conf": round(p.conf, 3),
                "box": norm(p.box),
                "foot": [round(fx / w, 4), round(fy / h, 4)],
            })
        return {"id": self.id, "frame_size": list(size), "people": people}

    # ---------- events ----------
    def _emit(self, event: dict) -> None:
        if self.hub is None:
            return
        event.setdefault("ts", time.time())
        event.setdefault("camera_id", self.id)
        self.hub.publish(event)

    def _check_state(self) -> None:
        state = STATE_OF_STATUS.get(self.status)
        if state is not None and state != self._cam_state:
            self._cam_state = state
            self._emit({"type": "camera", "status": state, "error": self.error or self.reader.error})

    def _update_tracks(self, persons: list[Person], now: float) -> None:
        for p in persons:
            tid = p.track_id
            if tid is None:
                continue
            t = self._tracks.get(tid)
            if t is None:
                t = self._tracks[tid] = {
                    "first": now, "last": now, "frames": 0, "items": Counter(),
                    "alert": False, "first_side": None, "last_side": None,
                }
            t["last"] = now
            t["frames"] += 1
            t["items"].update(set(p.items))
            if p.side != "unknown":
                t["first_side"] = t["first_side"] or p.side
                t["last_side"] = p.side
            if p.alert:
                t["alert"] = True
                if now - self._alerted.get(tid, 0.0) >= ALERT_REPEAT_S:
                    self._alerted[tid] = now
                    items = sorted(set(p.items))
                    self._emit({
                        "type": "alert",
                        "track_id": tid,
                        "items": [i for i in items if i.lower() in self.world.alerts],
                        "all_items": items,
                    })
        self._flush_stale(now)

    def _flush_stale(self, now: float) -> None:
        for tid in [tid for tid, t in self._tracks.items() if now - t["last"] > TRACK_END_S]:
            self._finish_track(tid)

    def _finish_track(self, tid: int) -> None:
        t = self._tracks.pop(tid, None)
        self._alerted.pop(tid, None)
        for key in [k for k in self._ppe_alerted if k[0] == tid]:
            self._ppe_alerted.pop(key, None)
        if t is None or t["frames"] < MIN_TRACK_FRAMES:
            return
        self._emit({
            "type": "track",
            "ts": t["last"],
            "track_id": tid,
            "first_ts": t["first"],
            "last_ts": t["last"],
            "duration": round(t["last"] - t["first"], 2),
            "frames": t["frames"],
            "items": {k: v for k, v in t["items"].most_common() if v >= MIN_ITEM_FRAMES},
            "alert": t["alert"],
            "first_side": t["first_side"],
            "last_side": t["last_side"],
        })

    def reset_counts(self) -> None:
        self.crossing.reset_counts()
        self._emit({"type": "reset"})

    # ---------- loop ----------
    def _publish(self, img: np.ndarray) -> None:
        width = int(self.cfg.get("stream_width", 1280))
        h, w = img.shape[:2]
        if w > width:
            img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(self.cfg.get("jpeg_quality", 75))])
        if ok:
            with self._jpeg_lock:
                self._jpeg = buf.tobytes()
                self._jpeg_seq += 1

    def _ensure_tracker(self) -> bool:
        if self.tracker is not None:
            return True
        self.status = "loading model"
        try:
            self.tracker = PersonTracker(self.person_weights, device=self.device)
            return True
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = f"model: {exc}"
            self._stop.wait(5.0)
            return False

    def _loop(self) -> None:
        last_seq = -1
        last_proc = 0.0
        last_placeholder = 0.0
        while not self._stop.is_set():
            frame, seq = self.reader.latest()
            now = time.perf_counter()
            if frame is None or seq == last_seq:
                if self.reader.status != "online" and now - last_placeholder > 1.0:
                    self.status = self.reader.status
                    self._check_state()
                    self._flush_stale(time.time())
                    self._publish(_placeholder(f"{self.id}: {self.reader.status}", self.reader.error or self.cfg["source"][:60]))
                    last_placeholder = now
                time.sleep(0.005)
                continue

            min_dt = 1.0 / max(1.0, float(self.cfg.get("process_fps", 15)))
            if now - last_proc < min_dt:
                time.sleep(min(0.005, min_dt - (now - last_proc)))
                continue
            dt = now - last_proc
            last_proc = now
            last_seq = seq

            try:
                self._process(frame)
                self.error = None
            except Exception as exc:  # noqa: BLE001
                self.status = "error"
                self.error = str(exc)
                self._check_state()
                self._publish(_placeholder(f"{self.id}: processing error", str(exc)))
                self._stop.wait(1.0)
                continue
            self._check_state()
            if 0 < dt < 5:
                self.fps = 0.85 * self.fps + 0.15 * (1.0 / dt) if self.fps else 1.0 / dt

    def _ppe_in_zone(self, cx: float, cy: float, w: int, h: int) -> bool:
        zone = self.crossing.zone_px(w, h)
        if not zone:
            return False
        poly = np.array(zone, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.pointPolygonTest(poly, (float(cx), float(cy)), False) >= 0

    def _person_in_ppe_zone(self, p: Person, w: int, h: int) -> bool:
        """Foot side == in, or body center inside the polygon (covers dead-band / feet outside)."""
        if p.track_id is None:
            return False
        if p.side == "in":
            return True
        x1, y1, x2, y2 = p.box
        return self._ppe_in_zone((x1 + x2) / 2.0, (y1 + y2) / 2.0, w, h)

    def _assign_ppe(self, findings: list[PpeFinding], persons: list[Person], w: int, h: int) -> list[PpeFinding]:
        """Attach findings to persons inside the zone; drop the rest unless their own center is in the zone.

        The zone is drawn on the floor (foot points), so a hardhat on an inside person usually sits above
        the polygon: ownership, not the finding's position, decides. An owner set by the crop pass is kept
        while that person is still inside; otherwise the smallest enlarged person box containing the center.
        Alerts / people[].ppe only for owned findings; unowned in-zone findings are still drawn.
        """
        for p in persons:
            p.ppe = []
            p.ppe_alert = False
        if not findings:
            return []
        inside = [p for p in persons if self._person_in_ppe_zone(p, w, h)]
        by_id = {p.track_id: p for p in inside}
        kept: list[PpeFinding] = []
        for f in findings:
            cx = (f.box[0] + f.box[2]) / 2.0
            cy = (f.box[1] + f.box[3]) / 2.0
            best: Person | None = by_id.get(f.owner) if f.owner is not None else None
            if best is None:
                best_area = float("inf")
                for p in inside:
                    x1, y1, x2, y2 = expand_box(p.box, w, h)
                    if x1 <= cx <= x2 and y1 <= cy <= y2:
                        area = (p.box[2] - p.box[0]) * (p.box[3] - p.box[1])
                        if area < best_area:
                            best, best_area = p, area
            if best is not None:
                f.owner = best.track_id
                best.ppe.append(f)
                best.ppe_alert = best.ppe_alert or f.alert
            elif self._ppe_in_zone(cx, cy, w, h):
                f.owner = None
            else:
                continue
            kept.append(f)
        return kept

    def _emit_ppe_alerts(self, persons: list[Person], now: float) -> None:
        for p in persons:
            tid = p.track_id
            if tid is None or not p.ppe_alert:
                continue
            negatives = sorted({f.label for f in p.ppe if f.alert})
            if not negatives:
                continue
            key = (tid, ",".join(negatives))
            if now - self._ppe_alerted.get(key, 0.0) < PPE_ALERT_REPEAT_S:
                continue
            self._ppe_alerted[key] = now
            self._emit({
                "type": "ppe_alert",
                "track_id": tid,
                "items": negatives,
                "all_items": sorted({f.label for f in p.ppe}),
            })

    def _process(self, frame: np.ndarray) -> None:
        cfg = self.cfg
        h, w = frame.shape[:2]
        persons: list[Person] = []
        t0 = time.perf_counter()

        if cfg.get("detect_enabled", True):
            if not self._ensure_tracker():
                return
            persons = self.tracker.track(frame, conf=float(cfg["conf"]), imgsz=int(cfg["imgsz"]))
            for ev in self.crossing.update(persons, w, h):
                self._emit({"type": "crossing", **ev})

        objects = self.objects
        world_on = self.world.enabled and cfg.get("world_enabled", True) and self.world.status == "ready"
        per_person = self.world.scope == "person"
        if world_on:
            every_n = max(1, int(self.world.cfg.get("every_n", 3)))
            if self._world_tick % every_n == 0:
                if per_person:
                    self.items.update(self.world.detect(frame, keep_rivals=True), persons, self.world.cfg)
                else:
                    objects = self.world.detect(frame)
            self._world_tick += 1
        else:
            objects = []
            self.items.clear()
        show_conf = float(self.world.cfg.get("show_conf", 0.0))
        if per_person:
            objects = self.items.render(persons, show_conf)
        else:
            objects = [o for o in objects if o.conf >= show_conf]
            if self.world.cfg.get("attach_to_person", True):
                attach_objects(persons, objects)

        ppe_findings = self.ppe_findings
        ppe_on = (
            self.ppe_svc is not None
            and self.ppe_svc.enabled
            and self.ppe_svc.status == "ready"
            and self.crossing.ppe_active
        )
        if ppe_on:
            every_n = max(1, int(self.ppe_svc.cfg.get("every_n", 3)))
            if self._ppe_tick % every_n == 0:
                if self.ppe_svc.mode == "crop":
                    targets = [(p.track_id, p.box) for p in persons if self._person_in_ppe_zone(p, w, h)]
                    found = self.ppe_svc.detect_people(frame, targets)
                else:
                    found = self.ppe_svc.detect(frame)
                ppe_findings = self._assign_ppe(found, persons, w, h)
            else:
                # Refresh owner flags on current persons from last findings (sides may have changed)
                ppe_findings = self._assign_ppe(ppe_findings, persons, w, h)
            self._ppe_tick += 1
        else:
            ppe_findings = []
            for p in persons:
                p.ppe = []
                p.ppe_alert = False

        self.infer_ms = (time.perf_counter() - t0) * 1000.0
        self.status = "running"
        now = time.time()
        self._update_tracks(persons, now)
        if ppe_on:
            self._emit_ppe_alerts(persons, now)

        zone_mode = self.crossing.mode == "zone"
        draw_geom = self.crossing.active
        line_px = self.crossing.line_px(w, h) if draw_geom and not zone_mode else None
        zone_px = self.crossing.zone_px(w, h) if draw_geom and zone_mode else None
        inward = (cfg.get("crossing") or {}).get("inward_point")
        inward_px = (float(inward["x"]) * w, float(inward["y"]) * h) if isinstance(inward, dict) else None
        cs = self.crossing.stats()
        hud = [f"{cfg.get('name', self.id)}  |  {self.fps:4.1f} fps  |  people {len(persons)}"]
        if self.crossing.active:
            tasks = ",".join(self.crossing.tasks()) or "-"
            hud.append(f"INSIDE {cs['inside']}   OUTSIDE {cs['outside']}   tasks {tasks}")
        if world_on:
            n_alert = sum(1 for p in persons if p.alert)
            hud.append(f"objects {len(objects)}" + (f"   ALERT {n_alert}" if n_alert else ""))
            if self.world.cfg.get("clip_verify"):
                c = self.world.clip
                hud.append(
                    f"CLIP {c.status}  keep {c.last_kept} drop {c.last_dropped}"
                    + (f"  {c.last_ms:.0f}ms" if c.last_ms else "")
                )
        if ppe_on:
            n_owned = sum(1 for f in ppe_findings if f.owner is not None)
            n_neg = sum(1 for f in ppe_findings if f.alert)
            hud.append(
                f"PPE {len(ppe_findings)} ({n_owned} on person)"
                + (f"   NO {n_neg}" if n_neg else "")
                + (f"   {self.ppe_svc.last_infer_ms:.0f}ms" if self.ppe_svc and self.ppe_svc.last_infer_ms else "")
            )
        elif self.crossing.has_task("ppe"):
            st = self.ppe_svc.status if self.ppe_svc else "none"
            hud.append(f"PPE off ({st})")
        img = render(
            frame,
            persons=persons,
            objects=objects,
            line_px=line_px,
            inward_px=inward_px,
            zone_px=zone_px,
            hud=hud,
            show_skeleton=bool(cfg.get("show_skeleton", True)),
            show_boxes=bool(cfg.get("show_boxes", True)),
            ppe=ppe_findings,
        )
        self.persons = persons
        self.objects = objects
        self.ppe_findings = ppe_findings
        self._publish(img)
