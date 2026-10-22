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

from .paths import MASK_MODEL_DEFAULT, PPE_MODEL_DEFAULT, resolve_model

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
HANDS = (("L", 7, 9), ("R", 8, 10))  # (side, elbow, wrist): each hand is judged on its own
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
        if len(pts) >= 2:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            spread = max(max(p[0] for p in pts) - min(p[0] for p in pts),
                         max(p[1] for p in pts) - min(p[1] for p in pts))
            s = max(spread * 1.6, ph * 0.12, 12.0)
            return [(cx - s / 2, cy - s * 0.75, cx + s / 2, cy + s * 0.35)]  # extends up: where a hat sits
        head = _head_from_shoulders(kpts, box)  # face turned away: the head still sits on the shoulders
        return [head] if head is not None else []
    return [b for b in (hand_box(kpts, box, side) for side, _, _ in HANDS) if b is not None]


def hand_box(kpts, box, side: str) -> tuple[float, float, float, float] | None:
    """Box around one hand ("L" / "R"): the stretch just past the wrist along the forearm; None if not visible."""
    _, e_i, w_i = next(h for h in HANDS if h[0] == side)
    wr = _kpt(kpts, w_i)
    if wr is None:
        return None
    el = _kpt(kpts, e_i)
    if el is not None:
        fx, fy = wr[0] - el[0], wr[1] - el[1]
        r = max(0.25 * math.hypot(fx, fy), 6.0)
        cx, cy = wr[0] + 0.3 * fx, wr[1] + 0.3 * fy
    else:
        r = max((box[3] - box[1]) * 0.04, 6.0)
        cx, cy = wr
    return cx - r, cy - r, cx + r, cy + r


def hand_key(side: str) -> str:
    """Evidence key of one hand: gloves are tracked per hand, not per person."""
    return f"gloves_{side}"


def hand_state(frame: np.ndarray, box, person_mask=None) -> str | None:
    """"covered" / "bare" for one hand box; None in the undecided band or when the hand is not on the person.

    `person_mask` = (x0, y0, bool array) from PpeService.person_masks; without it the whole circle is judged.
    """
    m = hand_skin(frame, box, person_mask)
    if m is None:
        return None
    skin_all, cover, skin_person = m
    if person_mask is None:
        if skin_all >= SKIN_BARE:
            return "bare"
        return "covered" if skin_all < SKIN_COVERED else None
    if cover >= MASK_MIN_COVER:
        if skin_person >= SKIN_BARE_PERSON:
            return "bare"
        return "covered" if skin_person < SKIN_COVERED_PERSON else None
    return "covered" if skin_all < SKIN_COVERED else None  # hand off the mask: only "no skin anywhere" counts


def to_rel(box, owner) -> tuple[float, float, float, float]:
    """Box in owner-box units (0..1 inside the owner), to redraw it on the owner's next box."""
    ow, oh = max(1.0, owner[2] - owner[0]), max(1.0, owner[3] - owner[1])
    return (box[0] - owner[0]) / ow, (box[1] - owner[1]) / oh, (box[2] - owner[0]) / ow, (box[3] - owner[1]) / oh


def from_rel(rel, owner) -> tuple[float, float, float, float]:
    ow, oh = owner[2] - owner[0], owner[3] - owner[1]
    return owner[0] + rel[0] * ow, owner[1] + rel[1] * oh, owner[0] + rel[2] * ow, owner[1] + rel[3] * oh


def _head_from_shoulders(kpts, box) -> tuple[float, float, float, float] | None:
    """Head box estimated from both shoulders (back / turned-away view), clipped to the person box top."""
    sl, sr = _kpt(kpts, 5), _kpt(kpts, 6)
    if sl is None or sr is None:
        return None
    sw = abs(sl[0] - sr[0])
    if sw < 8:
        return None
    cx = (sl[0] + sr[0]) / 2.0
    sy = min(sl[1], sr[1])
    top = max(box[1], sy - 1.1 * sw)
    hw = 0.3 * sw
    return cx - hw, top, cx + hw, max(top + 0.6 * sw, sy - 0.2 * sw)


# Appearance check on the part itself. On this camera the model is blind both ways (gloves / hardhat score
# < 0.04 worn or not), so "no positive seen" says nothing; the pixels do. Measured on the exhibition camera
# with the hand circle (wrist + 0.3 forearm, radius 0.25 forearm): bare hands 54-60 % skin, dark gloves
# 0-2 % (also over the tan leather chair, which a larger box mistook for skin).
SKIN_BARE = 0.25  # >= this share of skin pixels: bare hand
SKIN_COVERED = 0.08  # < this: covered (glove)
# With a person mask (yolo26n-seg on the PPE crop): skin share among the person's own pixels in the hand circle.
# Measured: bare hands 0.84-0.86, gloves 0.00. The mask often leaves a thin hanging hand out; when under
# MASK_MIN_COVER of the circle is person, the hand is not judged bare (that is the hand-over-chair case:
# 0 % person, 34 % "skin" = the leather), but "no skin at all" still means covered.
MASK_MIN_COVER = 0.2
SKIN_BARE_PERSON = 0.4
SKIN_COVERED_PERSON = 0.1
MASK_MIN_IOU = 0.3  # seg instance must overlap the tracked person box this much to be that person
CROWN_BARE = 0.5  # crown band (above the eyes) mostly hair / skin: bare head
CROWN_COVERED = 0.4  # crown band mostly bright or coloured non-skin: hardhat shell


def _skin_mask(region: np.ndarray) -> np.ndarray:
    ycc = cv2.cvtColor(region, cv2.COLOR_BGR2YCrCb)
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h, s = hsv[..., 0], hsv[..., 1]
    vivid_red = (s > 150) & ((h < 8) | (h > 170))  # red desks / objects fall in the YCrCb skin range; skin is less saturated
    return (cv2.inRange(ycc, (40, 135, 85), (255, 180, 135)) > 0) & ~vivid_red


def _box_px(shape, box) -> tuple[int, int, int, int] | None:
    h, w = shape[:2]
    x1, y1, x2, y2 = int(max(0, box[0])), int(max(0, box[1])), int(min(w, box[2])), int(min(h, box[3]))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return x1, y1, x2, y2


def _region(frame: np.ndarray, box) -> np.ndarray | None:
    px = _box_px(frame.shape, box)
    return None if px is None else frame[px[1]:px[3], px[0]:px[2]]


def hand_skin(frame: np.ndarray, box, person_mask=None) -> tuple[float, float, float] | None:
    """(skin share in the hand circle, share of the circle on the person mask, skin share on those pixels).

    The circle is inscribed in the hand box (corners are whatever the hand rests on). Without a mask the
    last two are 1.0 and the first value.
    """
    px = _box_px(frame.shape, box)
    if px is None:
        return None
    x1, y1, x2, y2 = px
    reg = frame[y1:y2, x1:x2]
    h, w = reg.shape[:2]
    circle = np.zeros((h, w), np.uint8)
    cv2.circle(circle, (w // 2, h // 2), max(1, min(w, h) // 2), 1, -1)
    inside = circle > 0
    skin = _skin_mask(reg)
    skin_all = float(skin[inside].mean())
    if person_mask is None:
        return skin_all, 1.0, skin_all
    mx, my, m = person_mask
    on = np.zeros((h, w), bool)  # person mask clipped to this region
    ax1, ay1 = max(x1, mx), max(y1, my)
    ax2, ay2 = min(x2, mx + m.shape[1]), min(y2, my + m.shape[0])
    if ax2 > ax1 and ay2 > ay1:
        on[ay1 - y1:ay2 - y1, ax1 - x1:ax2 - x1] = m[ay1 - my:ay2 - my, ax1 - mx:ax2 - mx]
    on &= inside
    cover = float(on.sum() / max(inside.sum(), 1))
    return skin_all, cover, float(skin[on].mean()) if on.any() else 0.0


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


def crown_cover(frame: np.ndarray, kpts, person_box=None) -> tuple[float, float] | None:
    """(share bare-looking: hair or skin, share hat-looking: bright or saturated non-skin) in the crown band.

    Face visible: band above the eyes. Turned away: upper half of the head estimated from the shoulders.
    """
    box = crown_box(kpts)
    if box is None and person_box is not None:
        head = _head_from_shoulders(kpts, person_box)
        if head is not None:
            box = (head[0], head[1], head[2], head[1] + 0.5 * (head[3] - head[1]))
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
    if kind == "gloves":  # person summary of the per-hand states (the worker keeps evidence per hand)
        states = [hand_state(frame, b) for b in part_boxes(kind, kpts, box)]
        if "bare" in states:
            return "bare"
        return "covered" if states and all(st == "covered" for st in states) else None
    if not part_boxes(kind, kpts, box):
        return None
    cc = crown_cover(frame, kpts, box)
    if cc is None:
        return None
    bare, shell = cc
    if crown_box(kpts) is None:
        # Head placed from the shoulders (face turned away): rough, and on a bent person it lands on the wall
        # behind, which looks like a bright shell. Only dark hair is trusted there, as "bare".
        return "bare" if bare >= CROWN_BARE else None
    if shell >= CROWN_COVERED:
        return "covered"
    return "bare" if bare >= CROWN_BARE else None


class PpeEvidence:
    """Per-track history of PPE runs: "pos" (positive seen), "neg" (part visible, no positive), None (not visible)."""

    def __init__(self) -> None:
        self.runs: dict[str, deque] = {}
        self.last_rel: dict[str, tuple] = {}  # key -> last seen part box relative to the person box

    def add(self, kind: str, obs: str | None) -> None:
        # A run where the part is hidden (turned away, occluded) says nothing: keep the last judgements,
        # so the state survives the person turning around instead of fading out.
        if obs is not None:
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
        self.seg = None  # person segmentation for the glove check; None = judge the whole hand circle
        self.last_mask_ms = 0.0
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
            self._load_seg(YOLO)
            self.status = "ready"
            self.error = None
            print(f"[ppe] ready with classes {[id_to_name[i] for i in self._class_ids]} ({name})")
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            print(f"[ppe] load failed: {exc}")

    def _load_seg(self, yolo_cls) -> None:
        """Optional: a missing / broken mask model only disables the mask, PPE keeps running."""
        if not self.cfg.get("person_mask", True):
            return
        name = self.cfg.get("mask_model") or MASK_MODEL_DEFAULT
        try:
            seg = yolo_cls(str(resolve_model(name)))
            if seg.task != "segment":
                raise RuntimeError(f"{name} is a {seg.task} model, not segment")
            self.seg = seg
            print(f"[ppe] person masks with {name}")
        except Exception as exc:  # noqa: BLE001
            self.seg = None
            print(f"[ppe] person masks off ({name}: {exc})")

    def person_masks(self, frame: np.ndarray, people: list[tuple[int | None, tuple]]) -> dict[int, tuple]:
        """{track_id: (x0, y0, bool mask)} for each person, from one batched seg pass on the PPE crops.

        Each crop may show several people; the instance overlapping the tracked box most is that person's.
        """
        if self.seg is None or not self.cfg.get("person_mask", True) or not people:
            return {}
        h, w = frame.shape[:2]
        crops, metas = [], []
        for tid, box in people:
            if tid is None or box[3] - box[1] < MIN_CROP_PX:
                continue
            x1, y1, x2, y2 = expand_box(box, w, h)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            crops.append(frame[y1:y2, x1:x2])
            metas.append((tid, box, x1, y1))
        if not crops:
            return {}
        with self._infer_lock:
            t0 = time.perf_counter()
            results = self.seg.predict(source=crops, imgsz=int(self.cfg.get("mask_imgsz", 640)), conf=0.25,
                                       classes=[0], retina_masks=True, device=self.device, verbose=False)
            self.last_mask_ms = (time.perf_counter() - t0) * 1000.0
        out: dict[int, tuple] = {}
        for res, (tid, box, x0, y0) in zip(results, metas):
            if res.masks is None or res.boxes is None or len(res.boxes) == 0:
                continue
            local = (box[0] - x0, box[1] - y0, box[2] - x0, box[3] - y0)
            ious = [_iou(tuple(b), local) for b in res.boxes.xyxy.cpu().numpy()]
            k = int(np.argmax(ious))
            if ious[k] < MASK_MIN_IOU:
                continue
            out[tid] = (x0, y0, res.masks.data[k].cpu().numpy() > 0.5)
        return out

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
            "person_mask": self.seg is not None,
            "last_mask_ms": round(self.last_mask_ms, 1),
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
