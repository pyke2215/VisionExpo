"""ByteTrack for detected objects on the whole frame (one instance per camera).

Ultralytics' STrack ids come from one global counter that the person trackers also use, and every
BYTETracker() resets it; object tracks get their own counter so person track ids are never disturbed.
Detections are already filtered by the per-label thresholds, so every box counts as a high-score one.
"""

from __future__ import annotations

import itertools
import sys
from types import SimpleNamespace

import numpy as np
from ultralytics.trackers.byte_tracker import BYTETracker, STrack

TRACK_BUFFER = 15  # detector runs a lost object track is kept and can be re-found with the same id
MATCH_THRESH = 0.8  # IoU distance: a track continues with a box overlapping its predicted box by >= 0.2


class _ObjTrack(STrack):
    # Fallback for older Ultralytics that still use a class-level counter on STrack.
    _ids = itertools.count(1)

    @staticmethod
    def next_id() -> int:
        return next(_ObjTrack._ids)


class _ObjByteTracker(BYTETracker):
    track_class = _ObjTrack

    def reset_id(self) -> None:
        # Ultralytics 8.4+ keeps IDs on the tracker (`self._ids`) and never shares them with
        # person ByteTrack. Do not call BaseTrack.reset_id() — that would renumber people.
        self._ids = iter(range(1, sys.maxsize))


class _Dets:
    """The Results-like view BYTETracker reads: xywh (center), conf, cls and boolean indexing."""

    def __init__(self, xyxy: np.ndarray, conf: np.ndarray) -> None:
        self.xyxy = xyxy
        self.conf = conf
        self.cls = np.zeros(len(conf), np.float32)

    @property
    def xywh(self) -> np.ndarray:
        x1, y1, x2, y2 = self.xyxy.T
        return np.stack([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1], axis=1)

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, idx) -> "_Dets":
        return _Dets(self.xyxy[idx], self.conf[idx])


class ObjectTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        args = SimpleNamespace(
            track_high_thresh=0.0,
            track_low_thresh=0.0,
            new_track_thresh=0.0,
            track_buffer=TRACK_BUFFER,
            match_thresh=MATCH_THRESH,
            fuse_score=False,
        )
        self._bt = _ObjByteTracker(args)

    def update(self, boxes: list[tuple[float, float, float, float]], confs: list[float]) -> list[int | None]:
        """Track id for each input box (None if it was not associated this run)."""
        xyxy = np.asarray(boxes, np.float32).reshape(-1, 4)
        self._bt.update(_Dets(xyxy, np.asarray(confs, np.float32).reshape(-1)))
        ids: list[int | None] = [None] * len(boxes)
        # New tracks stay unconfirmed until their second hit but already have an id: report them too.
        for t in self._bt.tracked_stracks:
            if t.frame_id == self._bt.frame_id and t.idx is not None and 0 <= int(t.idx) < len(ids):
                ids[int(t.idx)] = int(t.track_id)
        return ids
