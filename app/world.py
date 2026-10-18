"""Shared open-vocabulary object detector (YOLOE, or YOLO-World when WORLD_BASE_MODEL is a -world model).

The vocabulary is baked into models/<WORLD_BAKED_MODEL>: each class embedding is the average of the text
embeddings of its prompts (synonyms), pre-computed once with the model's text encoder (MobileCLIP for YOLOE,
CLIP for YOLO-World), so the exhibition machine runs offline. A sidecar .json records what was baked; a
different vocabulary or base model triggers a rebuild, which needs the text encoder (internet the first time).
Enabling/disabling single classes only filters predictions (`classes=`), no re-encoding needed.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import numpy as np

from .paths import MODELS_DIR, VOCABULARY_FILE, WORLD_BAKED_MODEL, WORLD_BASE_MODEL, resolve_model
from .vocabulary import (
    alert_labels,
    class_prompts,
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
BAKED_META = (MODELS_DIR / WORLD_BAKED_MODEL).with_suffix(".json")


@dataclass
class WorldObject:
    label: str
    conf: float
    box: tuple[float, float, float, float]
    alert: bool = False
    owner: int | None = None  # track id of the person carrying this object
    rel: tuple[float, float, float, float] | None = None  # box relative to the owner's box (0..1 of its w/h)
    category: str = ""
    hidden: bool = False  # suppress category (clothing / hair): evidence against look-alikes, never shown
    clip: float | None = None  # CLIP crop–label similarity when clip_verify is on


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-6, area_a + area_b - inter)


def _is_yoloe(name: str) -> bool:
    return "yoloe" in str(name).lower()


def _model_class(name: str):
    from ultralytics import YOLOE, YOLOWorld

    return YOLOE if _is_yoloe(name) else YOLOWorld


def _signature(classes: list[str], prompts: dict[str, list[str]], base: str = WORLD_BASE_MODEL) -> dict:
    return {"base": base, "classes": [[c, prompts.get(c) or [c]] for c in classes]}


def bake_vocabulary(classes: list[str], prompts: dict[str, list[str]], device: str = "0", base: str = WORLD_BASE_MODEL):
    """Average each class's prompt embeddings into the base model, save it; returns the loaded baked model."""
    import torch

    cls = _model_class(base)
    model = cls(str(resolve_model(base)))
    try:
        model.to("cuda" if device not in ("cpu", "") and _cuda_ok() else "cpu")
    except Exception:  # noqa: BLE001
        pass
    flat = [p for c in classes for p in prompts.get(c) or [c]]
    pe = model.get_text_pe(flat) if _is_yoloe(base) else model.model.get_text_pe(flat)  # (1, prompts, dim)
    feats, i = [], 0
    for c in classes:
        n = len(prompts.get(c) or [c])
        v = pe[:, i:i + n].mean(dim=1)
        feats.append(v / v.norm(dim=-1, keepdim=True))
        i += n
    emb = torch.stack(feats, dim=1)
    if _is_yoloe(base):
        model.set_classes(list(classes), emb)
    else:
        model.model.txt_feats = emb.to(next(model.model.parameters()).device)
        model.model.model[-1].nc = len(classes)
        model.model.names = list(classes)
        model.model.clip_model = None  # keep the saved file small; embeddings already in txt_feats
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out = MODELS_DIR / WORLD_BAKED_MODEL
    model.save(str(out))
    BAKED_META.write_text(json.dumps(_signature(classes, prompts, base), ensure_ascii=False, indent=1), encoding="utf-8")
    return cls(str(out))


def _cuda_ok() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


class WorldService:
    def __init__(self, cfg: dict, device: str = "0") -> None:
        from .clip_verify import ClipVerifier

        self.cfg = dict(cfg)
        self.device = device
        self.model = None
        self.status = "idle"  # idle | loading | ready | building | error
        self.error: str | None = None
        self.clip = ClipVerifier()
        self._set_vocabulary(load_vocabulary(VOCABULARY_FILE))
        self._infer_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.last_infer_ms = 0.0
        if self.cfg.get("enabled"):
            self.ensure_loaded_async()
        if self.cfg.get("clip_verify"):
            self.clip.ensure_loaded_async(self.device)

    def _set_vocabulary(self, doc: dict) -> None:
        self.vocab_doc = doc
        self.classes: list[str] = flatten_classes(doc)
        self.prompts = class_prompts(doc)
        self.vi = vi_labels(doc)
        self.alerts = alert_labels(doc)
        self.suppress = suppress_labels(doc)
        self.categories = label_categories(doc)
        self.clip.set_vocabulary(self.prompts)

    # ---------- config ----------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def update_config(self, cfg: dict) -> None:
        self.cfg = dict(cfg)
        if self.enabled:
            self.ensure_loaded_async()
        if self.cfg.get("clip_verify"):
            self.clip.ensure_loaded_async(self.device)
            self.clip.set_vocabulary(self.prompts)

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

    def _baked_is_current(self) -> bool:
        try:
            meta = json.loads(BAKED_META.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return meta == json.loads(json.dumps(_signature(self.classes, self.prompts)))

    def _load(self) -> None:
        try:
            baked = MODELS_DIR / WORLD_BAKED_MODEL
            model = None
            if baked.is_file():
                model = _model_class(WORLD_BASE_MODEL)(str(baked))
                if not self._baked_is_current():
                    print("[world] baked vocabulary differs from config/vocabulary.json, rebuilding…")
                    try:
                        model = bake_vocabulary(self.classes, self.prompts, self.device)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[world] rebuild failed ({exc}); keeping baked vocabulary")
            else:
                print(f"[world] no baked model, building from {WORLD_BASE_MODEL} (needs the text encoder + internet once)…")
                model = bake_vocabulary(self.classes, self.prompts, self.device)
            self.model = model
            self.status = "ready"
            self.error = None
            print(f"[world] ready with {len(self._model_names())} classes ({WORLD_BASE_MODEL})")
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            print(f"[world] load failed: {exc}")

    def rebuild_async(self, vocab_doc: dict) -> None:
        """Save new vocabulary and re-encode it in the background (needs the text encoder)."""
        save_vocabulary(VOCABULARY_FILE, vocab_doc)
        self._set_vocabulary(vocab_doc)
        with self._state_lock:
            self.status = "building"

        def run() -> None:
            try:
                model = bake_vocabulary(self.classes, self.prompts, self.device)
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

    def _objects(self, res) -> list[WorldObject]:
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
                    box=(float(b[0]), float(b[1]), float(b[2]), float(b[3])),
                    alert=alert,
                    category=self.categories.get(key, ""),
                    hidden=key in self.suppress,
                )
            )
        return out

    def _dedup(self, found: list[WorldObject], keep_rivals: bool = False) -> list[WorldObject]:
        """Keep the most confident box per region.

        keep_rivals=False (frame scope): one box per region; a hidden clothing box absorbs weaker accessory
            guesses it covers, then hidden labels are dropped.
        keep_rivals=True (person scope): only boxes of the same class collapse; rival classes on one region
            (hat vs hair, knife vs phone) and hidden labels are kept for ItemTracker to arbitrate.
        """
        found.sort(key=lambda o: o.conf, reverse=True)
        kept: list[WorldObject] = []
        for o in found:
            dup = False
            for k in kept:
                if keep_rivals and k.label != o.label and k.category != o.category:
                    continue
                iou = _iou(k.box, o.box)
                if iou >= (SUPPRESS_IOU if k.hidden != o.hidden else DEDUP_IOU):
                    dup = True
                    break
            if not dup:
                kept.append(o)
        return kept if keep_rivals else [o for o in kept if not o.hidden]

    def detect(self, frame: np.ndarray, keep_rivals: bool = False) -> list[WorldObject]:
        """Whole-frame detection; the model letterboxes the frame to `imgsz` itself."""
        results = self._predict([frame])
        if not results:
            return []
        found = self._dedup(self._objects(results[0]), keep_rivals)
        if self.cfg.get("clip_verify") and found:
            found = self.clip.filter(frame, found, float(self.cfg.get("clip_min", 0.22)))
        return found

    def info(self) -> dict:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "error": self.error,
            "scope": self.scope,
            "base_model": WORLD_BASE_MODEL,
            "model_classes": len(self._model_names()) if self.model is not None else 0,
            "vocabulary_classes": len(self.classes),
            "alert_labels": sorted(self.alerts),
            "suppress_labels": sorted(self.suppress),
            "category_conf": self._category_conf(),
            "last_infer_ms": round(self.last_infer_ms, 1),
            "clip": self.clip.info(),
            "config": self.cfg,
        }
