"""Owns camera workers and the shared YOLO-World service; applies settings changes."""

from __future__ import annotations

import threading

from .db import Database
from .events import EventHub
from .paths import DATABASE_FILE, resolve_model
from .settings_store import RESTART_FIELDS, SettingsStore
from .worker import CameraWorker
from .world import WorldService


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
        self.world = WorldService(store.get("world") or {}, device=self.device)
        self.hub = EventHub()
        self.db = Database(DATABASE_FILE, cfg_getter=lambda: store.get("database") or {}, status_getter=self.status)
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

    def _initial_counts(self, cam_id: str, old: CameraWorker | None) -> tuple[int, int]:
        if old is not None:
            return old.crossing.entries, old.crossing.exits
        db_cfg = self.store.get("database") or {}
        if db_cfg.get("enabled", True) and db_cfg.get("restore_counts", True):
            try:
                return self.db.counts_since_reset(cam_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[manager] cannot restore counts for {cam_id}: {exc}")
        return 0, 0

    def _start_worker(self, cam: dict) -> None:
        with self._lock:
            old = self.workers.pop(cam["id"], None)
            if old is not None:
                old.stop()
            w = CameraWorker(
                cam,
                person_weights=self.person_weights,
                device=self.device,
                world=self.world,
                hub=self.hub,
                initial_counts=self._initial_counts(cam["id"], old),
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

    # ---------- world ----------
    def update_world(self, patch: dict) -> dict:
        data = self.store.update_global({"world": patch})
        self.world.update_config(data["world"])
        return data["world"]

    # ---------- status ----------
    def status(self) -> dict:
        cams = []
        totals = {"persons": 0, "inside": 0, "outside": 0, "entries": 0, "exits": 0, "objects": 0, "alerts": 0}
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
            totals["objects"] += sum(s["objects"].values())
            totals["alerts"] += s["alerts"]
        return {"cameras": cams, "totals": totals, "world": self.world.info(), "device": self.device}
