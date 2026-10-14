"""Shared YOLO-World open-vocabulary detector.

The vocabulary is baked into models/yolo_world_exhibition.pt (text embeddings pre-computed with CLIP),
so the exhibition machine runs offline without CLIP. Enabling/disabling single classes only filters
predictions (`classes=`), no re-encoding needed. Adding new words triggers a rebuild that needs CLIP.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from .paths import MODELS_DIR, VOCABULARY_FILE, WORLD_BAKED_MODEL, WORLD_BASE_MODEL, resolve_model
from .vocabulary import (
    alert_labels,
    flatten_classes,
    label_categories,
    load_vocabulary,
    save_vocabulary,
    suppress_labels,
    vi_labels,
)

DEDUP_IOU = 0.6
SUPPRESS_IOU = 0.7  # a hidden clothing box must overlap this much to absorb an accessory guess
HIDDEN_CONF = 0.15  # hidden labels (hair, clothing) only count as evidence against look-alikes, so accept them low


@dataclass
class WorldObject:
    label: str
    conf: float
    box: tuple[float, float, float, float]
    alert: bool = False
    owner: int | None = None  # track id of the person whose crop produced this object
    rel: tuple[float, float, float, float] | None = None  # box relative to the owner's box (0..1 of its w/h)
    category: str = ""
    hidden: bool = False  # suppress category (clothing / hair): evidence against look-alikes, never shown


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-6, area_a + area_b - inter)


MIN_OWNER_COVER = 0.3


def _best_owner(box, persons):
    """Person whose box covers the largest fraction of `box`; ties go to the nearest box center."""
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    best, best_key = None, None
    for p in persons:
        x1, y1, x2, y2 = p.box
        inter = max(0.0, min(box[2], x2) - max(box[0], x1)) * max(0.0, min(box[3], y2) - max(box[1], y1))
        cover = inter / area
        if cover < MIN_OWNER_COVER:
            continue
        dist = ((cx - (x1 + x2) / 2.0) / max(1.0, x2 - x1)) ** 2 + ((cy - (y1 + y2) / 2.0) / max(1.0, y2 - y1)) ** 2
        key = (round(cover, 2), -dist)
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best


def bake_vocabulary(classes: list[str], device: str = "0", base: str = WORLD_BASE_MODEL):
    """Encode `classes` with CLIP into a YOLO-World model and save it; returns the loaded model."""
    from ultralytics import YOLOWorld

    model = YOLOWorld(str(resolve_model(base)))
    try:
        model.to("cuda" if device not in ("cpu", "") and _cuda_ok() else "cpu")
    except Exception:  # noqa: BLE001
        pass
    model.set_classes(list(classes))
    model.model.clip_model = None  # keep the saved file small; embeddings already in txt_feats
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out = MODELS_DIR / WORLD_BAKED_MODEL
    model.save(str(out))
    return YOLOWorld(str(out))


def _cuda_ok() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


class WorldService:
    def __init__(self, cfg: dict, device: str = "0") -> None:
        self.cfg = dict(cfg)
        self.device = device
        self.model = None
        self.status = "idle"  # idle | loading | ready | building | error
        self.error: str | None = None
        self.vocab_doc = load_vocabulary(VOCABULARY_FILE)
        self.classes: list[str] = flatten_classes(self.vocab_doc)
        self.vi = vi_labels(self.vocab_doc)
        self.alerts = alert_labels(self.vocab_doc)
        self.suppress = suppress_labels(self.vocab_doc)
        self.categories = label_categories(self.vocab_doc)
        self._infer_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.last_infer_ms = 0.0
        if self.cfg.get("enabled"):
            self.ensure_loaded_async()

    # ---------- config ----------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def update_config(self, cfg: dict) -> None:
        self.cfg = dict(cfg)
        if self.enabled:
            self.ensure_loaded_async()

    def disabled_set(self) -> set[str]:
        return {str(c).lower() for c in self.cfg.get("disabled_classes") or []}

    def class_filter(self) -> list[int] | None:
        """Indices of enabled classes in model order; None = all enabled; [] = nothing enabled."""
        if self.model is None:
            return []
        names = self._model_names()
        disabled = self.disabled_set()
        if not disabled:
            return None
        return [i for i, n in enumerate(names) if n.lower() not in disabled]

    def _model_names(self) -> list[str]:
        names = self.model.names if self.model is not None else {}
        if isinstance(names, dict):
            return [str(names[k]) for k in sorted(names)]
        return [str(n) for n in names]

    # ---------- loading ----------
    def ensure_loaded_async(self) -> None:
        with self._state_lock:
            if self.model is not None or self.status in ("loading", "building"):
                return
            self.status = "loading"
        threading.Thread(target=self._load, name="world-load", daemon=True).start()

    def _load(self) -> None:
        try:
            from ultralytics import YOLOWorld

            baked = MODELS_DIR / WORLD_BAKED_MODEL
            model = None
            if baked.is_file():
                model = YOLOWorld(str(baked))
                names = list(model.names.values()) if isinstance(model.names, dict) else list(model.names)
                if [n.lower() for n in names] != self.classes:
                    print("[world] baked vocabulary differs from config/vocabulary.json, rebuilding…")
                    try:
                        model = bake_vocabulary(self.classes, self.device)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[world] rebuild failed ({exc}); keeping baked vocabulary")
            else:
                print("[world] no baked model, building from base model (needs CLIP + internet once)…")
                model = bake_vocabulary(self.classes, self.device)
            self.model = model
            self.status = "ready"
            self.error = None
            print(f"[world] ready with {len(self._model_names())} classes")
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            print(f"[world] load failed: {exc}")

    def rebuild_async(self, vocab_doc: dict) -> None:
        """Save new vocabulary and re-encode it in the background (needs CLIP)."""
        save_vocabulary(VOCABULARY_FILE, vocab_doc)
        self.vocab_doc = vocab_doc
        self.classes = flatten_classes(vocab_doc)
        self.vi = vi_labels(vocab_doc)
        self.alerts = alert_labels(vocab_doc)
        self.suppress = suppress_labels(vocab_doc)
        self.categories = label_categories(vocab_doc)
        with self._state_lock:
            self.status = "building"

        def run() -> None:
            try:
                model = bake_vocabulary(self.classes, self.device)
                with self._infer_lock:
                    self.model = model
                self.status = "ready"
                self.error = None
            except Exception as exc:  # noqa: BLE001
                self.status = "error" if self.model is None else "ready"
                self.error = f"rebuild failed: {exc}"
                print(f"[world] {self.error}")

        threading.Thread(target=run, name="world-rebuild", daemon=True).start()

    # ---------- inference ----------
    @property
    def scope(self) -> str:
        return "frame" if self.cfg.get("scope") == "frame" else "person"

    def _predict(self, sources: list[np.ndarray]):
        """Run one batched prediction; None if disabled / not ready / every class filtered out."""
        if not self.enabled or self.model is None or self.status != "ready" or not sources:
            return None
        cls_filter = self.class_filter()
        if cls_filter is not None and not cls_filter:
            return None
        with self._infer_lock:
            t0 = time.perf_counter()
            results = self.model.predict(
                source=sources,
                conf=self._min_conf(),
                imgsz=int(self.cfg.get("imgsz", 640)),
                classes=cls_filter,
                device=self.device,
                verbose=False,
            )
            self.last_infer_ms = (time.perf_counter() - t0) * 1000.0
        return results

    def _category_conf(self) -> dict[str, float]:
        raw = self.cfg.get("category_conf") or {}
        return {str(k): float(v) for k, v in raw.items() if v is not None}

    def _conf(self, label: str) -> float:
        """Threshold for one label: its category's own value, else alert_conf (weapons) or conf."""
        cat_conf = self._category_conf().get(self.categories.get(label, ""))
        if cat_conf is not None:
            return cat_conf
        if label in self.suppress:
            return HIDDEN_CONF
        base = float(self.cfg.get("conf", 0.3))
        return float(self.cfg.get("alert_conf", base)) if label in self.alerts else base

    def _min_conf(self) -> float:
        base = float(self.cfg.get("conf", 0.3))
        hidden = [HIDDEN_CONF] if self.suppress - self.disabled_set() else []
        return min([base, float(self.cfg.get("alert_conf", base)), *self._category_conf().values(), *hidden])

    def _objects(self, res, dx: float = 0.0, dy: float = 0.0) -> list[WorldObject]:
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            return []
        names = res.names
        min_px = float(self.cfg.get("min_obj_px", 14))
        out = []
        for b, c, s in zip(boxes.xyxy.cpu().numpy(), boxes.cls.int().cpu().numpy(), boxes.conf.cpu().numpy()):
            label = str(names[int(c)])
            key = label.lower()
            alert = key in self.alerts
            if float(s) < self._conf(key):
                continue
            if min(b[2] - b[0], b[3] - b[1]) < min_px and key not in self.suppress:
                continue
            out.append(
                WorldObject(
                    label=label,
                    conf=float(s),
                    box=(float(b[0]) + dx, float(b[1]) + dy, float(b[2]) + dx, float(b[3]) + dy),
                    alert=alert,
                    category=self.categories.get(key, ""),
                    hidden=key in self.suppress,
                )
            )
        return out

    def _dedup(self, found: list[WorldObject], keep_rivals: bool = False) -> list[WorldObject]:
        """Keep the most confident box per region and owner.

        Same object seen by several crops, or one region given several synonyms (handbag / tote bag) on the
        same person, collapses to one box.
        keep_rivals=False (frame scope): one box per region; a hidden clothing box absorbs weaker accessory
            guesses it covers, then hidden labels are dropped.
        keep_rivals=True (person scope): only boxes of the same category collapse; rival categories on one
            region (hat vs hair, knife vs phone) and hidden labels are kept for ItemTracker to arbitrate.
        """
        found.sort(key=lambda o: o.conf, reverse=True)
        kept: list[WorldObject] = []
        for o in found:
            dup = False
            for k in kept:
                if k.owner != o.owner and k.label != o.label:
                    continue
                if keep_rivals and k.label != o.label and k.category != o.category:
                    continue
                iou = _iou(k.box, o.box)
                if iou >= (SUPPRESS_IOU if k.hidden != o.hidden else DEDUP_IOU):
                    dup = True
                    break
            if not dup:
                kept.append(o)
        return kept if keep_rivals else [o for o in kept if not o.hidden]

    def detect(self, frame: np.ndarray) -> list[WorldObject]:
        """Whole-frame detection (scope = "frame")."""
        results = self._predict([frame])
        return self._dedup(self._objects(results[0])) if results else []

    def detect_in_persons(self, frame: np.ndarray, persons) -> tuple[list[WorldObject], set[int]]:
        """Detect only inside (slightly enlarged) crops of tracked persons; each object is owned by that person.

        Returns the objects and the track ids whose crops were examined (others were too small / over the limit).
        """
        h, w = frame.shape[:2]
        margin = float(self.cfg.get("crop_margin", 0.1))
        min_px = float(self.cfg.get("min_person_px", 60))
        max_n = int(self.cfg.get("max_persons", 8))
        cands = [
            p for p in persons
            if p.track_id is not None and (p.box[3] - p.box[1]) >= min_px
        ]
        cands.sort(key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1]), reverse=True)
        cands = cands[:max_n]

        crops, origins, owners = [], [], []
        for p in cands:
            x1, y1, x2, y2 = p.box
            mx, my = (x2 - x1) * margin, (y2 - y1) * margin * 0.5
            cx1, cy1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
            cx2, cy2 = int(min(w, x2 + mx)), int(min(h, y2 + my))
            if cx2 - cx1 < 8 or cy2 - cy1 < 8:
                continue
            crops.append(np.ascontiguousarray(frame[cy1:cy2, cx1:cx2]))
            origins.append((cx1, cy1))
            owners.append(p)

        results = self._predict(crops)
        if not results:
            return [], set()
        found: list[WorldObject] = []
        for res, (ox, oy) in zip(results, origins):
            for o in self._objects(res, ox, oy):
                # A crop may include a neighbour: give the object to the person covering it most.
                p = _best_owner(o.box, owners)
                if p is None:
                    continue
                px1, py1, px2, py2 = p.box
                pw, ph = max(1.0, px2 - px1), max(1.0, py2 - py1)
                o.owner = p.track_id
                o.rel = ((o.box[0] - px1) / pw, (o.box[1] - py1) / ph, (o.box[2] - px1) / pw, (o.box[3] - py1) / ph)
                found.append(o)

        return self._dedup(found, keep_rivals=True), {p.track_id for p in owners}

    def info(self) -> dict:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "error": self.error,
            "scope": self.scope,
            "model_classes": len(self._model_names()) if self.model is not None else 0,
            "vocabulary_classes": len(self.classes),
            "alert_labels": sorted(self.alerts),
            "suppress_labels": sorted(self.suppress),
            "category_conf": self._category_conf(),
            "last_infer_ms": round(self.last_infer_ms, 1),
            "config": self.cfg,
        }
