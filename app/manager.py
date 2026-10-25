"""Owns camera workers and the shared PPE detector (PpeService); applies settings changes."""

from __future__ import annotations

import threading
import time

from .db import Database
from .events import EventHub
from .paths import DATABASE_FILE, resolve_model
from .ppe import PpeService
from .settings_store import RESTART_FIELDS, SettingsStore
from .worker import CameraWorker


def _usable_device(device: str) -> str:
    if device.lower() in ("cpu", "mps"):
        return device
    try:
        import torch

        if torch.cuda.is_available():
            return device
    except Exception:  # noqa: BLE001
        pass
    print(f"[manager] CUDA not available, falling back from device '{device}' to cpu")
    return "cpu"


class CameraManager:
    def __init__(self, store: SettingsStore, device_override: str | None = None) -> None:
        self.store = store
        self.device = _usable_device(device_override or str(store.get("device") or "0"))
        self.person_weights = resolve_model(store.get("person_model"))
        self.ppe = PpeService(store.get("ppe") or {}, device=self.device)
        self.hub = EventHub()
        self.db = Database(
            DATABASE_FILE,
            cfg_getter=lambda: store.get("database") or {},
            status_getter=self.status,
            heat_getter=self._take_heat,
        )
        self._visitors_cache: tuple[float, int] = (0.0, 0)
        self.hub.subscribe(self.db.on_event)
        self.workers: dict[str, CameraWorker] = {}
        self._lock = threading.RLock()

    def start(self) -> None:
        self.db.start()
        for cam in self.store.cameras():
            if cam.get("enabled"):
                self._start_worker(cam)

    def shutdown(self) -> None:
        with self._lock:
            for w in list(self.workers.values()):
                w.stop()
            self.workers.clear()
        self.db.stop()

    def _initial_counts(self, cam_id: str, old: CameraWorker | None) -> dict:
        """{zone_id | "_line" | None: (entries, exits)} since the last reset (see CrossingCounter.restore_counts)."""
        if old is not None:
            return old.crossing.count_state()
        db_cfg = self.store.get("database") or {}
        if db_cfg.get("enabled", True) and db_cfg.get("restore_counts", True):
            try:
                return self.db.counts_since_reset(cam_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[manager] cannot restore counts for {cam_id}: {exc}")
        return {}

    def _start_worker(self, cam: dict) -> None:
        with self._lock:
            old = self.workers.pop(cam["id"], None)
            if old is not None:
                old.stop()
            w = CameraWorker(
                cam,
                person_weights=self.person_weights,
                device=self.device,
                ppe=self.ppe,
                hub=self.hub,
                initial_counts=self._initial_counts(cam["id"], old),
                stats_cfg=lambda: self.store.get("stats") or {},
            )
            self.workers[cam["id"]] = w
            w.start()

    def reset_counts(self, cam_id: str | None = None) -> None:
        with self._lock:
            targets = [self.workers[cam_id]] if cam_id in self.workers else list(self.workers.values()) if cam_id is None else []
        for w in targets:
            w.reset_counts()

    def _stop_worker(self, cam_id: str) -> None:
        with self._lock:
            w = self.workers.pop(cam_id, None)
        if w is not None:
            w.stop()

    def worker(self, cam_id: str) -> CameraWorker | None:
        with self._lock:
            return self.workers.get(cam_id)

    # ---------- camera CRUD ----------
    def add_camera(self, cam: dict) -> dict:
        new = self.store.add_camera(cam)
        if new.get("enabled"):
            self._start_worker(new)
        return new

    def update_camera(self, cam_id: str, patch: dict) -> dict:
        old, new = self.store.update_camera(cam_id, patch)
        needs_restart = any(old.get(k) != new.get(k) for k in RESTART_FIELDS)
        if not new.get("enabled"):
            self._stop_worker(cam_id)
        elif needs_restart or self.worker(cam_id) is None:
            self._start_worker(new)
        else:
            self.worker(cam_id).apply_config(new)  # type: ignore[union-attr]
        return new

    def remove_camera(self, cam_id: str) -> None:
        self._stop_worker(cam_id)
        self.store.remove_camera(cam_id)

    def restart_camera(self, cam_id: str) -> None:
        cam = self.store.camera(cam_id)
        if cam is None:
            raise KeyError(cam_id)
        if cam.get("enabled"):
            self._start_worker(cam)

    def update_section(self, key: str, patch: dict) -> dict:
        return self.store.update_global({key: patch})[key]

    # ---------- PPE ----------
    def update_ppe(self, patch: dict) -> dict:
        data = self.store.update_global({"ppe": patch})
        self.ppe.update_config(data["ppe"])
        return data["ppe"]

    # ---------- zones / heatmap ----------
    def _take_heat(self) -> dict:
        with self._lock:
            workers = list(self.workers.items())
        return {cid: w.take_heat() for cid, w in workers}

    def stats_cfg(self) -> dict:
        return {"visitor_min_s": 10, **(self.store.get("stats") or {})}

    def visitors_today(self) -> int:
        """Visitors (tracks flagged visitor) since local midnight; cached 30 s since status() runs often."""
        now = time.time()
        ts, n = self._visitors_cache
        if now - ts < 30.0:
            return n
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        try:
            n = sum(r["visitors"] for r in self.db.visitors_hourly(ts_from=midnight))
        except Exception as exc:  # noqa: BLE001
            print(f"[manager] visitors_today: {exc}")
        self._visitors_cache = (now, n)
        return n

    def heatmap(self, cam_id: str, ts_from=None, ts_to=None) -> dict | None:
        """Stored hours in range + what the worker has not handed to the database yet (when the range is open)."""
        h = self.db.heatmap(camera_id=cam_id, ts_from=ts_from, ts_to=ts_to)
        w = self.worker(cam_id)
        live = w.heat_snapshot() if w is not None and (ts_to is None or ts_to > time.time()) else None
        if live is not None:
            rows, cols = live.shape
            if h is None or (h["cols"], h["rows"]) != (cols, rows):
                h = h or {"cols": cols, "rows": rows, "grid": [0.0] * (cols * rows)}
            if (h["cols"], h["rows"]) == (cols, rows):
                h["grid"] = [a + float(b) for a, b in zip(h["grid"], live.ravel())]
        if h is not None:
            h["max"] = max(h["grid"]) if h["grid"] else 0
        return h

    # ---------- status ----------
    def status(self) -> dict:
        cams = []
        totals = {
            "persons": 0, "inside": 0, "outside": 0, "entries": 0, "exits": 0,
            "ppe_alerts": 0,
        }
        for cam in self.store.cameras():
            w = self.worker(cam["id"])
            if w is None:
                cams.append({"id": cam["id"], "name": cam["name"], "status": "disabled" if not cam["enabled"] else "stopped"})
                continue
            s = w.stats()
            cams.append(s)
            totals["persons"] += s["persons"]
            totals["entries"] += s["crossing"]["entries"]
            totals["exits"] += s["crossing"]["exits"]
            totals["inside"] += s["crossing"].get("inside") or 0
            totals["outside"] += s["crossing"].get("outside") or 0
            totals["ppe_alerts"] += s.get("ppe_alerts", 0)
            for z in s["crossing"].get("zones") or []:
                k = totals.setdefault("zones", {}).setdefault(z["kind"], {"inside": 0, "crowded": 0})
                k["inside"] += z["inside"]
                k["crowded"] += bool(z["crowded"])
        totals["visitors_today"] = self.visitors_today()
        return {
            "cameras": cams,
            "totals": totals,
            "ppe": self.ppe.info(),
            "device": self.device,
        }
