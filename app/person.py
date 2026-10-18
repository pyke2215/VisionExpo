"""Person detection + ByteTrack tracking (one model instance per camera: tracker state lives in the predictor)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from ultralytics import YOLO

LEFT_ANKLE = 15
RIGHT_ANKLE = 16
KPT_MIN_CONF = 0.35


@dataclass
class Person:
    track_id: int | None
    box: tuple[float, float, float, float]
    conf: float
    keypoints: np.ndarray | None = None  # (17, 3) x, y, conf
    side: str = "unknown"  # crossing side: "in" | "out" | "unknown"
    items: list[str] = field(default_factory=list)  # object labels attached to this person
    alert: bool = False  # carries an item from an alert category (weapons)

    def foot_point(self) -> tuple[float, float]:
        """Ankle midpoint if visible, otherwise bottom-center of the box."""
        if self.keypoints is not None and len(self.keypoints) > RIGHT_ANKLE:
            pts = [self.keypoints[i] for i in (LEFT_ANKLE, RIGHT_ANKLE) if self.keypoints[i][2] >= KPT_MIN_CONF]
            if pts:
                return float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))
        x1, _, x2, y2 = self.box
        return (x1 + x2) / 2.0, y2


class PersonTracker:
    def __init__(self, weights, device: str = "0") -> None:
        self.model = YOLO(str(weights))
        self.device = device
        self.is_pose = self.model.task == "pose"
        names = self.model.names or {}
        self.person_cls = next((int(k) for k, v in names.items() if str(v).lower() == "person"), 0)

    def track(self, frame: np.ndarray, *, conf: float, imgsz: int) -> list[Person]:
        kwargs = dict(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=conf,
            imgsz=imgsz,
            device=self.device,
            verbose=False,
        )
        if not self.is_pose:
            kwargs["classes"] = [self.person_cls]
        res = self.model.track(**kwargs)[0]
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        ids = boxes.id.int().cpu().numpy() if boxes.id is not None else [None] * len(xyxy)
        kpts = None
        if self.is_pose and res.keypoints is not None and res.keypoints.data is not None:
            kpts = res.keypoints.data.cpu().numpy()
        out: list[Person] = []
        for i, box in enumerate(xyxy):
            out.append(
                Person(
                    track_id=int(ids[i]) if ids[i] is not None else None,
                    box=tuple(float(v) for v in box),
                    conf=float(confs[i]),
                    keypoints=kpts[i] if kpts is not None and i < len(kpts) else None,
                )
            )
        return out

    def reset(self) -> None:
        predictor = getattr(self.model, "predictor", None)
        for tracker in getattr(predictor, "trackers", None) or []:
            tracker.reset()
