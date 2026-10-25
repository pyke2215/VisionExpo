"""Overlay rendering on frames (OpenCV, ASCII text only)."""

from __future__ import annotations

import math
import unicodedata

import cv2
import numpy as np

from .person import Person
from .zones import ZONE_COLORS as ZONE_COLORS_HEX

SKELETON = [
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4),
]
SIDE_COLORS = {"in": (80, 200, 60), "out": (0, 140, 255), "unknown": (200, 200, 200)}
LINE_COLOR = (255, 0, 200)
PPE_OK_COLOR = (60, 200, 120)
ALERT_COLOR = (40, 40, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def ascii_text(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _label(img: np.ndarray, text: str, org: tuple[int, int], color, scale: float = 0.55, thick: int = 1) -> None:
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thick)
    x, y = org
    y = max(th + 4, y)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 6, y + base - 2), color, -1)
    cv2.putText(img, text, (x + 3, y - 3), FONT, scale, (20, 20, 20), thick, cv2.LINE_AA)


def draw_line(img: np.ndarray, line_px, inward_px: tuple[float, float] | None, scale: float) -> None:
    (ax, ay), (bx, by) = line_px
    a = (int(ax), int(ay))
    b = (int(bx), int(by))
    t = max(2, int(3 * scale))
    cv2.line(img, a, b, LINE_COLOR, t, cv2.LINE_AA)
    cv2.circle(img, a, t + 3, LINE_COLOR, -1)
    cv2.circle(img, b, t + 3, LINE_COLOR, -1)
    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
    nx, ny = -(by - ay), (bx - ax)
    norm = math.hypot(nx, ny) or 1.0
    nx, ny = nx / norm, ny / norm
    if inward_px is not None:
        if (inward_px[0] - mx) * nx + (inward_px[1] - my) * ny < 0:
            nx, ny = -nx, -ny
    length = 50 * scale
    tip = (int(mx + nx * length), int(my + ny * length))
    cv2.arrowedLine(img, (int(mx), int(my)), tip, SIDE_COLORS["in"], t, cv2.LINE_AA, tipLength=0.35)
    _label(img, "IN", (tip[0] + 4, tip[1] + 4), SIDE_COLORS["in"], 0.6 * scale, max(1, int(scale)))


# BGR per zone kind, from the shared "#rrggbb" palette in zones.py
ZONE_COLORS = {k: (int(v[5:7], 16), int(v[3:5], 16), int(v[1:3], 16)) for k, v in ZONE_COLORS_HEX.items()}


def draw_zone(img: np.ndarray, zone_px, scale: float, kind: str = "booth", label: str = "ZONE") -> None:
    """Polygon edges, vertices and a name tag; no fill, so the picture inside keeps its true colours."""
    pts = np.array([[int(x), int(y)] for x, y in zone_px], dtype=np.int32)
    color = ZONE_COLORS.get(kind, ZONE_COLORS["other"])
    t = max(2, int(3 * scale))
    cv2.polylines(img, [pts], True, color, t, cv2.LINE_AA)
    for x, y in pts:
        cv2.circle(img, (int(x), int(y)), t + 2, color, -1)
    top = min(pts, key=lambda p: (p[1], p[0]))
    _label(img, ascii_text(label), (int(top[0]) + 6, int(top[1]) - 6), color, 0.6 * scale, max(1, int(scale)))


def render(
    frame: np.ndarray,
    *,
    persons: list[Person],
    line_px,
    inward_px,
    hud: list[str],
    show_skeleton: bool,
    show_boxes: bool,
    zones=None,
    ppe=None,
) -> np.ndarray:
    img = frame.copy()
    h, w = img.shape[:2]
    scale = max(0.6, min(2.0, w / 1280.0))
    thick = max(1, int(2 * scale))

    for kind, label, pts in zones or []:  # (kind, label, polygon px)
        draw_zone(img, pts, scale, kind, label)
    if line_px is not None:
        draw_line(img, line_px, inward_px, scale)

    for finding in ppe or []:
        x1, y1, x2, y2 = (int(v) for v in finding.box)
        pc = ALERT_COLOR if finding.alert else PPE_OK_COLOR
        cv2.rectangle(img, (x1, y1), (x2, y2), pc, thick)
        _label(img, f"{finding.label} {finding.conf:.2f}", (x1, y1), pc, 0.45 * scale)

    for p in persons:
        color = ALERT_COLOR if p.ppe_alert else SIDE_COLORS.get(p.side, SIDE_COLORS["unknown"])
        x1, y1, x2, y2 = (int(v) for v in p.box)
        if show_boxes:
            cv2.rectangle(img, (x1, y1), (x2, y2), color, thick + 1)
        if show_skeleton and p.keypoints is not None:
            k = p.keypoints
            for i, j in SKELETON:
                if i < len(k) and j < len(k) and k[i][2] > 0.35 and k[j][2] > 0.35:
                    cv2.line(img, (int(k[i][0]), int(k[i][1])), (int(k[j][0]), int(k[j][1])), color, thick, cv2.LINE_AA)
            for x, y, c in k:
                if c > 0.35:
                    cv2.circle(img, (int(x), int(y)), thick + 1, (255, 255, 255), -1)
        fx, fy = p.foot_point()
        cv2.circle(img, (int(fx), int(fy)), thick + 3, color, -1)
        tag = f"#{p.track_id}" if p.track_id is not None else "person"
        if p.side != "unknown":
            tag += f" {p.side.upper()}"
        if p.ppe_alert:
            tag += " PPE"
        _label(img, tag, (x1, y1), color, 0.55 * scale, max(1, int(scale)))

    y = int(28 * scale)
    for line in hud:
        cv2.putText(img, ascii_text(line), (12, y), FONT, 0.7 * scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
        cv2.putText(img, ascii_text(line), (12, y), FONT, 0.7 * scale, (255, 255, 255), thick, cv2.LINE_AA)
        y += int(28 * scale)
    return img

