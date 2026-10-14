"""settings.json: global options + camera list, thread-safe load/save."""

from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any

from .paths import PERSON_MODEL_DEFAULT, SETTINGS_FILE, WORLD_BAKED_MODEL

CAMERA_DEFAULTS: dict[str, Any] = {
    "name": "",
    "source": "0",
    "enabled": True,
    "detect_enabled": True,
    "conf": 0.4,
    "imgsz": 640,
    "process_fps": 15,
    "stream_width": 1280,
    "jpeg_quality": 75,
    "show_skeleton": True,
    "show_boxes": True,
    "world_enabled": True,
    "crossing": {
        "enabled": True,
        "mode": "line",
        "points": [],
        "inward_point": None,
        "zone": [],
    },
}

GLOBAL_DEFAULTS: dict[str, Any] = {
    "device": "0",
    "person_model": PERSON_MODEL_DEFAULT,
    "world": {
        "enabled": False,
        "model": WORLD_BAKED_MODEL,
        "conf": 0.3,
        "alert_conf": 0.45,
        "category_conf": {"accessories": 0.6},  # per-category override; null = use conf / alert_conf
        "min_obj_px": 14,
        "show_conf": 0.6,  # objects below this (smoothed) confidence are tracked but not drawn / reported
        "label_margin": 1.5,  # rival readings of one region (hat vs hair): winner needs this x the runner-up
        "imgsz": 640,
        "every_n": 3,
        "disabled_classes": [],
        "attach_to_person": True,
        "scope": "person",
        "crop_margin": 0.1,
        "max_persons": 8,
        "min_person_px": 60,
        "confirm_hits": 2,
        "alert_hits": 3,
        "drop_misses": 3,
        "reject_static": True,
    },
    "websocket": {
        "enabled": True,
        "status_hz": 2.0,
        "people_hz": 5.0,
        "default_topics": ["status", "events"],
        "token": "",
    },
    "database": {
        "enabled": True,
        "sample_every_s": 10,
        "retention_days": 30,
        "save_tracks": True,
        "restore_counts": True,
    },
    "cameras": [
        {**copy.deepcopy(CAMERA_DEFAULTS), "id": "webcam", "name": "Webcam", "source": "0"},
    ],
}

# Changing these requires restarting the camera worker (new capture / model).
RESTART_FIELDS = {"source", "enabled"}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize_camera(cam: dict) -> dict:
    merged = _deep_merge(CAMERA_DEFAULTS, cam)
    merged["id"] = str(merged.get("id") or "").strip()
    merged["name"] = str(merged.get("name") or merged["id"])
    merged["source"] = str(merged.get("source") or "0").strip()
    merged["conf"] = float(min(max(float(merged["conf"]), 0.05), 0.95))
    merged["imgsz"] = int(min(max(int(merged["imgsz"]), 320), 1280)) // 32 * 32
    merged["process_fps"] = float(min(max(float(merged["process_fps"]), 1.0), 60.0))
    merged["stream_width"] = int(min(max(int(merged["stream_width"]), 320), 3840))
    merged["jpeg_quality"] = int(min(max(int(merged["jpeg_quality"]), 30), 95))
    return merged


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip().lower()).strip("_")
    return s or "camera"


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict:
        raw: dict = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8")) or {}
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[settings] cannot read {self.path}: {exc}; using defaults")
        data = _deep_merge(GLOBAL_DEFAULTS, {k: v for k, v in raw.items() if k != "cameras"})
        cams = raw.get("cameras") if "cameras" in raw else GLOBAL_DEFAULTS["cameras"]
        data["cameras"] = [normalize_camera(c) for c in cams if c.get("id")]
        self._data = data
        self._write()
        return data

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key))

    def update_global(self, patch: dict) -> dict:
        with self._lock:
            patch = {k: v for k, v in patch.items() if k != "cameras"}
            self._data = _deep_merge(self._data, patch)
            self._write()
            return copy.deepcopy(self._data)

    def cameras(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._data["cameras"])

    def camera(self, cam_id: str) -> dict | None:
        with self._lock:
            for c in self._data["cameras"]:
                if c["id"] == cam_id:
                    return copy.deepcopy(c)
        return None

    def unique_id(self, base: str) -> str:
        with self._lock:
            ids = {c["id"] for c in self._data["cameras"]}
        cand = slugify(base)
        i = 2
        while cand in ids:
            cand = f"{slugify(base)}_{i}"
            i += 1
        return cand

    def add_camera(self, cam: dict) -> dict:
        with self._lock:
            if not cam.get("id"):
                cam["id"] = self.unique_id(cam.get("name") or "camera")
            if any(c["id"] == cam["id"] for c in self._data["cameras"]):
                raise ValueError(f"camera id '{cam['id']}' already exists")
            norm = normalize_camera(cam)
            self._data["cameras"].append(norm)
            self._write()
            return copy.deepcopy(norm)

    def update_camera(self, cam_id: str, patch: dict) -> tuple[dict, dict]:
        """Return (old, new) camera dicts."""
        with self._lock:
            for i, c in enumerate(self._data["cameras"]):
                if c["id"] == cam_id:
                    patch = {k: v for k, v in patch.items() if k != "id"}
                    new = normalize_camera(_deep_merge(c, patch))
                    self._data["cameras"][i] = new
                    self._write()
                    return copy.deepcopy(c), copy.deepcopy(new)
        raise KeyError(cam_id)

    def remove_camera(self, cam_id: str) -> None:
        with self._lock:
            before = len(self._data["cameras"])
            self._data["cameras"] = [c for c in self._data["cameras"] if c["id"] != cam_id]
            if len(self._data["cameras"]) == before:
                raise KeyError(cam_id)
            self._write()
