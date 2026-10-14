"""One thread per camera: latest frame, person tracking, crossing count, YOLO-World, overlay, JPEG."""

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
from .world import WorldObject, WorldService

TRACK_END_S = 3.0  # a track unseen this long is considered gone and summarized
MIN_TRACK_FRAMES = 5  # shorter tracks are flicker, not visitors
MIN_ITEM_FRAMES = 3  # an item must be shown this many frames to count in the summary
ALERT_REPEAT_S = 30.0
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
        hub: EventHub | None = None,
        initial_counts: tuple[int, int] = (0, 0),
    ) -> None:
        self.cfg = dict(cfg)
        self.id = cfg["id"]
        self.person_weights = person_weights
        self.device = device
        self.world = world
        self.hub = hub
        self.crossing = CrossingCounter(cfg.get("crossing"))
        self.crossing.entries, self.crossing.exits = initial_counts
        self._tracks: dict[int, dict] = {}
        self._alerted: dict[int, float] = {}
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
        self.items = ItemTracker()
        self._world_tick = 0

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
                {"track_id": p.track_id, "side": p.side, "items": sorted(set(p.items)), "alert": p.alert}
                for p in persons
            ],
            "objects": dict(Counter(o.label for o in objects)),
            "alerts": sum(1 for p in persons if p.alert),
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
        people = []
        for p in self.persons:
            fx, fy = p.foot_point()
            people.append({
                "track_id": p.track_id,
                "side": p.side,
                "alert": p.alert,
                "items": sorted(set(p.items)),
                "objects": objs_by_owner.get(p.track_id, []) if p.track_id is not None else [],
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
                    found, examined = self.world.detect_in_persons(frame, persons)
                    self.items.update(found, examined, persons, self.world.cfg)
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
        self.infer_ms = (time.perf_counter() - t0) * 1000.0
        self.status = "running"
        self._update_tracks(persons, time.time())

        zone_mode = self.crossing.mode == "zone"
        line_px = self.crossing.line_px(w, h) if self.crossing.enabled and not zone_mode else None
        zone_px = self.crossing.zone_px(w, h) if self.crossing.enabled and zone_mode else None
        inward = (cfg.get("crossing") or {}).get("inward_point")
        inward_px = (float(inward["x"]) * w, float(inward["y"]) * h) if isinstance(inward, dict) else None
        cs = self.crossing.stats()
        hud = [f"{cfg.get('name', self.id)}  |  {self.fps:4.1f} fps  |  people {len(persons)}"]
        if self.crossing.enabled:
            hud.append(f"INSIDE {cs['inside']}   OUTSIDE {cs['outside']}")
        if world_on:
            n_alert = sum(1 for p in persons if p.alert)
            hud.append(f"objects {len(objects)}" + (f"   ALERT {n_alert}" if n_alert else ""))
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
        )
        self.persons = persons
        self.objects = objects
        self._publish(img)
