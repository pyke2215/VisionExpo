"""PPE detector: Hardhat / Gloves and their NO-* negatives (shared YOLO model).

Only these four classes are kept from yolo_ppe.pt. Positive findings are reported; NO-* set alert=True.
Zone filtering (person side == in) is done by the camera worker.

mode "crop" (default): one crop per in-zone person (box enlarged by PPE_MARGIN_*), batched and each upscaled
to imgsz. On a 1920x1080 frame letterboxed to 640 a head is ~25 px and the model (trained on close-ups)
finds nothing; on the crop it is ~4x larger. mode "frame": a single pass on the whole frame.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from .paths import PPE_MODEL_DEFAULT, resolve_model

# Model class names → (kind, is_negative)
PPE_CLASSES = {
    "Hardhat": ("hardhat", False),
    "NO-Hardhat": ("hardhat", True),
    "Gloves": ("gloves", False),
    "NO-Gloves": ("gloves", True),
}
# Hardhat boxes often sit above the head; expand the person box upward more than sideways.
PPE_MARGIN_X = 0.2
PPE_MARGIN_TOP = 0.45
PPE_MARGIN_BOTTOM = 0.1
MIN_CROP_PX = 48  # persons shorter than this are too small to judge, even upscaled
DEDUP_IOU = 0.5  # same-kind findings overlapping this much are one reading (keep the most confident)


def expand_box(box, w: int, h: int) -> tuple[int, int, int, int]:
    """Person box enlarged by the PPE margins, clipped to the frame."""
    x1, y1, x2, y2 = box
    pw, ph = x2 - x1, y2 - y1
    return (
        int(max(0, x1 - pw * PPE_MARGIN_X)),
        int(max(0, y1 - ph * PPE_MARGIN_TOP)),
        int(min(w, x2 + pw * PPE_MARGIN_X)),
        int(min(h, y2 + ph * PPE_MARGIN_BOTTOM)),
    )


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ---------- inferred negatives ----------
# The NO-* classes of yolo_ppe.pt do not transfer to overhead CCTV (a clear bare head scores < 0.03), while
# positives (a real hardhat / gloves) are what the model learned best. So a negative is inferred: the body
# part is clearly visible on the pose keypoints, yet no positive was found on that person for several runs.
KIND_LABELS = {"hardhat": ("Hardhat", "NO-Hardhat"), "gloves": ("Gloves", "NO-Gloves")}
HEAD_KPTS = (0, 1, 2, 3, 4)  # nose, eyes, ears
ARM_KPTS = ((7, 9), (8, 10))  # (elbow, wrist) left / right
KPT_VISIBLE = 0.5
EVIDENCE_WINDOW = 5  # PPE runs remembered per track and kind


def _kpt(kpts, i) -> tuple[float, float] | None:
    if kpts is None or len(kpts) <= i or kpts[i][2] < KPT_VISIBLE:
        return None
    return float(kpts[i][0]), float(kpts[i][1])


def part_boxes(kind: str, kpts, box) -> list[tuple[float, float, float, float]]:
    """Where the PPE of `kind` would be on this person, from visible keypoints (empty = part not visible)."""
    x1, y1, x2, y2 = box
    ph = max(1.0, y2 - y1)
    if kind == "hardhat":
        pts = [p for p in (_kpt(kpts, i) for i in HEAD_KPTS) if p is not None]
        if len(pts) < 2:
            return []
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        spread = max(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))
        s = max(spread * 1.6, ph * 0.12, 12.0)
        return [(cx - s / 2, cy - s * 0.75, cx + s / 2, cy + s * 0.35)]  # extends up: where a hat sits
    out = []
    for e_i, w_i in ARM_KPTS:
        wr = _kpt(kpts, w_i)
        if wr is None:
            continue
        el = _kpt(kpts, e_i)
        cx, cy = wr
        if el is not None:  # the hand continues past the wrist along the forearm
            cx, cy = wr[0] + 0.35 * (wr[0] - el[0]), wr[1] + 0.35 * (wr[1] - el[1])
        s = max(ph * 0.1, 12.0)
        out.append((cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2))
    return out


# Appearance check on the part itself. On this camera the model is blind both ways (gloves / hardhat score
# < 0.04 worn or not), so "no positive seen" says nothing; the pixels do. Measured on the exhibition camera:
# bare hand box 25-30 % skin, dark gloves 0-7 %.
SKIN_BARE = 0.15  # >= this share of skin pixels: bare hand
SKIN_COVERED = 0.10  # < this: covered (glove)
CROWN_BARE = 0.5  # crown band (above the eyes) mostly hair / skin: bare head
CROWN_COVERED = 0.4  # crown band mostly bright or coloured non-skin: hardhat shell


def _skin_mask(region: np.ndarray) -> np.ndarray:
    ycc = cv2.cvtColor(region, cv2.COLOR_BGR2YCrCb)
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h, s = hsv[..., 0], hsv[..., 1]
    vivid_red = (s > 150) & ((h < 8) | (h > 170))  # red desks / objects fall in the YCrCb skin range; skin is less saturated
    return (cv2.inRange(ycc, (40, 135, 85), (255, 180, 135)) > 0) & ~vivid_red


def _region(frame: np.ndarray, box) -> np.ndarray | None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = int(max(0, box[0])), int(max(0, box[1])), int(min(w, box[2])), int(min(h, box[3]))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return frame[y1:y2, x1:x2]


def hand_skin(frame: np.ndarray, box) -> float | None:
    # core of the hand box only: the margin is mostly whatever the hand rests on
    cx, cy, half = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, (box[2] - box[0]) * 0.35
    reg = _region(frame, (cx - half, cy - half, cx + half, cy + half))
    return None if reg is None else float(_skin_mask(reg).mean())


def crown_box(kpts) -> tuple[float, float, float, float] | None:
    """Band just above the eyes, ear to ear: forehead / hair when bare, the shell of a hardhat when worn."""
    pts = [p for p in (_kpt(kpts, i) for i in HEAD_KPTS) if p is not None]
    if len(pts) < 2:
        return None
    eyes = [p for p in (_kpt(kpts, 1), _kpt(kpts, 2)) if p is not None]
    ears = [p for p in (_kpt(kpts, 3), _kpt(kpts, 4)) if p is not None]
    xs = [p[0] for p in pts]
    if len(ears) == 2:
        span = abs(ears[0][0] - ears[1][0])  # frontal / back: ear to ear = head width
    else:  # profile: nose-to-ear spread is ~2/3 of the head width
        span = 1.6 * max(max(xs) - min(xs), math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]))
    span = max(span, 10.0)
    cx = (min(xs) + max(xs)) / 2.0
    ey = min(p[1] for p in eyes) if eyes else min(p[1] for p in pts) - 0.1 * span
    return cx - 0.5 * span, ey - 0.75 * span, cx + 0.5 * span, ey - 0.15 * span


def crown_cover(frame: np.ndarray, kpts) -> tuple[float, float] | None:
    """(share bare-looking: hair or skin, share hat-looking: bright or saturated non-skin) in the crown band."""
    box = crown_box(kpts)
    reg = None if box is None else _region(frame, box)
    if reg is None:
        return None
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    skin = _skin_mask(reg)
    dark = v < 80
    shell = ~skin & ~dark & ((s > 100) | (v > 180))
    return float((skin | dark).mean()), float(shell.mean())


def cover_state(kind: str, frame: np.ndarray, kpts, box) -> str | None:
    """"covered" / "bare" from the pixels of the part, None when undecided or not visible."""
    if kind == "gloves":
        skins = [s for s in (hand_skin(frame, b) for b in part_boxes(kind, kpts, box)) if s is not None]
        if not skins:
            return None
        if any(s >= SKIN_BARE for s in skins):
            return "bare"  # one bare hand is a missing glove
        return "covered" if all(s < SKIN_COVERED for s in skins) else None
    if not part_boxes(kind, kpts, box):
        return None
    cc = crown_cover(frame, kpts)
    if cc is None:
        return None
    bare, shell = cc
    if shell >= CROWN_COVERED:
        return "covered"
    return "bare" if bare >= CROWN_BARE else None


class PpeEvidence:
    """Per-track history of PPE runs: "pos" (positive seen), "neg" (part visible, no positive), None (not visible)."""

    def __init__(self) -> None:
        self.runs: dict[str, deque] = {}

    def add(self, kind: str, obs: str | None) -> None:
        self.runs.setdefault(kind, deque(maxlen=EVIDENCE_WINDOW)).append(obs)

    def missing(self, kind: str, confirm_runs: int) -> float | None:
        """Share of recent runs that saw the part bare, when the negative is confirmed; None otherwise."""
        runs = self.runs.get(kind)
        if not runs or "pos" in runs:
            return None
        neg = sum(1 for o in runs if o == "neg")
        return neg / EVIDENCE_WINDOW if neg >= confirm_runs else None

    def worn(self, kind: str, confirm_runs: int) -> float | None:
        """Share of recent runs that saw the item worn, when that is confirmed; None otherwise."""
        runs = self.runs.get(kind)
        pos = sum(1 for o in runs or () if o == "pos")
        return pos / EVIDENCE_WINDOW if pos >= confirm_runs else None


@dataclass
class PpeFinding:
    label: str
    conf: float
    box: tuple[float, float, float, float]
    alert: bool
    kind: str  # hardhat | gloves
    owner: int | None = None
    inferred: bool = False  # NO-* derived from visible keypoints + no positive (not a model box)


class PpeService:
    def __init__(self, cfg: dict, device: str = "0") -> None:
        self.cfg = dict(cfg)
        self.device = device
        self.model = None
        self.status = "idle"  # idle | loading | ready | error
        self.error: str | None = None
        self._infer_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.last_infer_ms = 0.0
        self._class_ids: list[int] = []
        if self.cfg.get("enabled", True):
            self.ensure_loaded_async()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    def update_config(self, cfg: dict) -> None:
        self.cfg = dict(cfg)
        if self.enabled:
            self.ensure_loaded_async()

    def ensure_loaded_async(self) -> None:
        with self._state_lock:
            if self.model is not None or self.status == "loading":
                return
            self.status = "loading"
        threading.Thread(target=self._load, name="ppe-load", daemon=True).start()

    def _load(self) -> None:
        try:
            from ultralytics import YOLO

            name = self.cfg.get("model") or PPE_MODEL_DEFAULT
            model = YOLO(str(resolve_model(name)))
            names = model.names or {}
            if isinstance(names, dict):
                id_to_name = {int(k): str(v) for k, v in names.items()}
            else:
                id_to_name = {i: str(n) for i, n in enumerate(names)}
            self._class_ids = sorted(i for i, n in id_to_name.items() if n in PPE_CLASSES)
            if not self._class_ids:
                raise RuntimeError(f"PPE model has none of {sorted(PPE_CLASSES)}; got {list(id_to_name.values())}")
            self.model = model
            self._id_to_name = id_to_name
            self.status = "ready"
            self.error = None
            print(f"[ppe] ready with classes {[id_to_name[i] for i in self._class_ids]} ({name})")
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            print(f"[ppe] load failed: {exc}")

    @property
    def mode(self) -> str:
        return "frame" if self.cfg.get("mode") == "frame" else "crop"

    @property
    def required(self) -> list[str]:
        """Kinds whose absence is reported (inferred NO-*); empty when infer_missing is off."""
        if not self.cfg.get("infer_missing", True):
            return []
        return [k for k in (self.cfg.get("require") or list(KIND_LABELS)) if k in KIND_LABELS]

    @property
    def confirm_runs(self) -> int:
        return int(min(max(int(self.cfg.get("confirm_runs", 3)), 1), EVIDENCE_WINDOW))

    def _predict(self, sources: list[np.ndarray]):
        with self._infer_lock:
            t0 = time.perf_counter()
            results = self.model.predict(
                source=sources,
                conf=float(self.cfg.get("conf", 0.25)),
                imgsz=int(self.cfg.get("imgsz", 640)),
                classes=self._class_ids,
                device=self.device,
                verbose=False,
            )
            self.last_infer_ms = (time.perf_counter() - t0) * 1000.0
        return results

    def _findings(self, res, dx: float = 0.0, dy: float = 0.0, owner: int | None = None) -> list[PpeFinding]:
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            return []
        out: list[PpeFinding] = []
        for b, c, s in zip(boxes.xyxy.cpu().numpy(), boxes.cls.int().cpu().numpy(), boxes.conf.cpu().numpy()):
            label = self._id_to_name.get(int(c), str(int(c)))
            meta = PPE_CLASSES.get(label)
            if meta is None:
                continue
            kind, negative = meta
            out.append(
                PpeFinding(
                    label=label,
                    conf=float(s),
                    box=(float(b[0]) + dx, float(b[1]) + dy, float(b[2]) + dx, float(b[3]) + dy),
                    alert=negative,
                    kind=kind,
                    owner=owner,
                )
            )
        return out

    def _ready(self) -> bool:
        if not self.enabled or self.model is None or self.status != "ready":
            if self.enabled:
                self.ensure_loaded_async()
            return False
        return True

    def detect(self, frame: np.ndarray) -> list[PpeFinding]:
        """Whole-frame pass (mode "frame")."""
        if not self._ready():
            return []
        return _dedup(self._findings(self._predict([frame])[0]))

    def detect_people(self, frame: np.ndarray, people: list[tuple[int | None, tuple]]) -> list[PpeFinding]:
        """Crop pass (mode "crop"): `people` = [(track_id, person box)]; findings come back with owner set.

        A crop also shows parts of neighbours, so a finding is kept only if its center lies inside the
        crop's own enlarged person box; overlapping readings across crops are merged by _dedup.
        """
        if not self._ready() or not people:
            return []
        h, w = frame.shape[:2]
        crops, metas = [], []
        for tid, box in people:
            if box[3] - box[1] < MIN_CROP_PX:
                continue
            x1, y1, x2, y2 = expand_box(box, w, h)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            crops.append(frame[y1:y2, x1:x2])
            metas.append((tid, (x1, y1, x2, y2)))
        if not crops:
            return []
        out: list[PpeFinding] = []
        for res, (tid, (x1, y1, x2, y2)) in zip(self._predict(crops), metas):
            for f in self._findings(res, x1, y1, tid):
                cx = (f.box[0] + f.box[2]) / 2.0
                cy = (f.box[1] + f.box[3]) / 2.0
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    out.append(f)
        return _dedup(out)

    def info(self) -> dict:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "error": self.error,
            "model": self.cfg.get("model") or PPE_MODEL_DEFAULT,
            "mode": self.mode,
            "required": self.required,
            "classes": [self._id_to_name[i] for i in self._class_ids] if self.model is not None else list(PPE_CLASSES),
            "last_infer_ms": round(self.last_infer_ms, 1),
            "config": self.cfg,
        }


def _dedup(findings: list[PpeFinding]) -> list[PpeFinding]:
    """One reading per region and kind: Hardhat vs NO-Hardhat on one head keeps the more confident."""
    kept: list[PpeFinding] = []
    for f in sorted(findings, key=lambda f: f.conf, reverse=True):
        if all(k.kind != f.kind or _iou(k.box, f.box) < DEDUP_IOU for k in kept):
            kept.append(f)
    return kept
