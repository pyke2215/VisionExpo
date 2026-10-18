"""Optional CLIP post-filter: score each YOLOE box crop against its predicted label.

Uses OpenAI CLIP ViT-B/32 (same package as setup.bat installs). Independent of MobileCLIP used
to bake YOLOE text prompts — image and text are encoded with this one model only.
"""

from __future__ import annotations

import threading
import time

import numpy as np

# Short noun prompts score in ~0.20–0.30 cosine; "a photo of a …" is the usual CLIP template.
_TEMPLATE = "a photo of a {}"


class ClipVerifier:
    def __init__(self) -> None:
        self.model = None
        self.device = "cpu"
        self.status = "idle"  # idle | loading | ready | error
        self.error: str | None = None
        self._text: dict[str, object] = {}  # label -> (dim,) tensor
        self._prompts: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.last_ms = 0.0
        self.last_kept = 0
        self.last_dropped = 0

    def ensure_loaded_async(self, device: str) -> None:
        self.device = device
        with self._lock:
            if self.model is not None or self.status == "loading":
                return
            self.status = "loading"
        threading.Thread(target=self._load, name="clip-load", daemon=True).start()

    def _load(self) -> None:
        try:
            import torch
            from ultralytics.nn.text_model import CLIP

            dev = torch.device("cuda" if self.device not in ("cpu", "") and torch.cuda.is_available() else "cpu")
            model = CLIP("ViT-B/32", device=dev)
            with self._lock:
                self.model = model
                self.status = "ready"
                self.error = None
            print(f"[clip] ready on {dev}")
            if self._prompts:
                self.set_vocabulary(self._prompts)
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            print(f"[clip] load failed: {exc}")

    def set_vocabulary(self, prompts: dict[str, list[str]]) -> None:
        """Cache one normalized text vector per label (average of its prompts)."""
        self._prompts = {str(k).lower(): [str(p) for p in (v or [k])] for k, v in prompts.items()}
        if self.model is None:
            return
        import torch

        texts, owners = [], []
        for label, ps in self._prompts.items():
            for p in ps:
                texts.append(_TEMPLATE.format(p))
                owners.append(label)
        if not texts:
            self._text = {}
            return
        with self._lock:
            tok = self.model.tokenize(texts)
            feats = self.model.encode_text(tok)  # (N, dim)
        by: dict[str, list] = {}
        for lab, row in zip(owners, feats):
            by.setdefault(lab, []).append(row)
        out = {}
        for lab, rows in by.items():
            v = torch.stack(rows, dim=0).mean(dim=0)
            out[lab] = v / v.norm(p=2)
        self._text = out

    def filter(self, frame: np.ndarray, objects: list, min_sim: float) -> list:
        """Drop detections whose crop–label CLIP similarity is below `min_sim`; set `o.clip` on keepers."""
        if not objects:
            self.last_kept = self.last_dropped = 0
            return objects
        if self.model is None or self.status != "ready":
            self.ensure_loaded_async(self.device)
            return objects  # pass-through until CLIP is ready
        if not self._text and self._prompts:
            self.set_vocabulary(self._prompts)

        from PIL import Image

        h, w = frame.shape[:2]
        rgb = frame[:, :, ::-1]
        kept: list[WorldObject] = []
        dropped = 0
        t0 = time.perf_counter()
        with self._lock:
            for o in objects:
                key = o.label.lower()
                txt = self._text.get(key)
                if txt is None:
                    kept.append(o)
                    continue
                x1, y1, x2, y2 = (int(v) for v in o.box)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 - x1 < 4 or y2 - y1 < 4:
                    dropped += 1
                    continue
                crop = Image.fromarray(rgb[y1:y2, x1:x2])
                img_f = self.model.encode_image(crop).squeeze(0)
                sim = float((img_f * txt).sum().item())
                if sim < min_sim:
                    dropped += 1
                    continue
                o.clip = sim
                kept.append(o)
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        self.last_kept = len(kept)
        self.last_dropped = dropped
        return kept

    def info(self) -> dict:
        return {
            "status": self.status,
            "error": self.error,
            "last_ms": round(self.last_ms, 1),
            "last_kept": self.last_kept,
            "last_dropped": self.last_dropped,
            "labels": len(self._text),
        }
