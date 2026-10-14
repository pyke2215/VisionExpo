"""Background frame reader: webcam index, RTSP/HTTP stream or video file, keeps only the latest frame."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

# RTSP over TCP avoids h264 "error while decoding MB" caused by dropped UDP packets.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay",
)

OPEN_TIMEOUT_MS = 8000
READ_TIMEOUT_MS = 8000


def source_kind(source: str) -> str:
    s = source.strip()
    if s.isdigit():
        return "webcam"
    low = s.lower()
    if low.startswith(("rtsp://", "rtsps://", "rtmp://", "http://", "https://", "udp://", "tcp://")):
        return "stream"
    return "file"


def open_capture(source: str) -> cv2.VideoCapture:
    """Open a capture or raise RuntimeError. Blocking for at most ~OPEN_TIMEOUT_MS on streams."""
    kind = source_kind(source)
    if kind == "webcam":
        idx = int(source)
        apis = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == "win32" else [cv2.CAP_ANY]
        for api in apis:
            cap = cv2.VideoCapture(idx, api)
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    return cap
            cap.release()
        raise RuntimeError(f"cannot open webcam index {idx}")

    if kind == "stream":
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS,
        ]
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError("cannot open stream (check URL, user/password, network)")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    path = Path(source)
    if not path.is_file():
        raise RuntimeError(f"video file not found: {source}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"cannot open video file: {source}")
    return cap


def grab_single_frame(source: str) -> np.ndarray:
    """Open, read one valid frame, close. Used by the 'test connection' button."""
    cap = open_capture(source)
    try:
        for _ in range(30):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                return frame
            time.sleep(0.05)
    finally:
        cap.release()
    raise RuntimeError("opened but no frame received")


class FrameReader:
    """Reads continuously in a thread, reconnects on failure, exposes the latest frame only."""

    def __init__(self, source: str, *, reconnect_delay_s: float = 3.0, max_fail_reads: int = 60) -> None:
        self.source = source
        self.kind = source_kind(source)
        self.reconnect_delay_s = reconnect_delay_s
        self.max_fail_reads = max_fail_reads
        self.status = "starting"
        self.error: str | None = None
        self.frame_size: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"reader-{source[:24]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)

    def latest(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._latest, self._seq

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            self.status = "connecting"
            try:
                cap = open_capture(self.source)
            except Exception as exc:  # noqa: BLE001
                self.status = "offline"
                self.error = str(exc)
                failures += 1
                self._stop.wait(min(30.0, self.reconnect_delay_s * failures))
                continue

            failures = 0
            self.status = "online"
            self.error = None
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            pace = 1.0 / fps if self.kind == "file" and 1.0 <= fps <= 120.0 else 0.0
            fails = 0
            next_t = time.perf_counter()
            while not self._stop.is_set():
                ok, frame = cap.read()
                if ok and frame is not None and frame.size > 0:
                    fails = 0
                    with self._lock:
                        self._latest = frame
                        self._seq += 1
                    self.frame_size = (frame.shape[1], frame.shape[0])
                    if pace:
                        next_t += pace
                        delay = next_t - time.perf_counter()
                        if delay > 0:
                            time.sleep(delay)
                        else:
                            next_t = time.perf_counter()
                    continue
                if self.kind == "file":
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                fails += 1
                if fails >= self.max_fail_reads:
                    self.status = "reconnecting"
                    self.error = "stream stalled"
                    break
                time.sleep(0.02)
            cap.release()
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_delay_s)
        self.status = "stopped"
