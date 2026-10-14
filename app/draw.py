"""Overlay rendering on frames (OpenCV, ASCII text only)."""

from __future__ import annotations

import math
import unicodedata

import cv2
import numpy as np

from .person import Person
from .world import WorldObject

SKELETON = [
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4),
]
SIDE_COLORS = {"in": (80, 200, 60), "out": (0, 140, 255), "unknown": (200, 200, 200)}
LINE_COLOR = (255, 0, 200)
OBJ_COLOR = (255, 200, 0)
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


def draw_zone(img: np.ndarray, zone_px, scale: float) -> None:
    pts = np.array([[int(x), int(y)] for x, y in zone_px], dtype=np.int32)
    overlay = img.copy()
    cv2.fillPoly(overlay, [pts], SIDE_COLORS["in"])
    cv2.addWeighted(overlay, 0.18, img, 0.82, 0, dst=img)
    t = max(2, int(3 * scale))
    cv2.polylines(img, [pts], True, LINE_COLOR, t, cv2.LINE_AA)
    for x, y in pts:
        cv2.circle(img, (int(x), int(y)), t + 2, LINE_COLOR, -1)
    top = min(pts, key=lambda p: (p[1], p[0]))
    _label(img, "ZONE", (int(top[0]) + 6, int(top[1]) - 6), SIDE_COLORS["in"], 0.6 * scale, max(1, int(scale)))


def render(
    frame: np.ndarray,
    *,
    persons: list[Person],
    objects: list[WorldObject],
    line_px,
    inward_px,
    hud: list[str],
    show_skeleton: bool,
    show_boxes: bool,
    zone_px=None,
) -> np.ndarray:
    img = frame.copy()
    h, w = img.shape[:2]
    scale = max(0.6, min(2.0, w / 1280.0))
    thick = max(1, int(2 * scale))

    if zone_px is not None:
        draw_zone(img, zone_px, scale)
    if line_px is not None:
        draw_line(img, line_px, inward_px, scale)

    for obj in objects:
        x1, y1, x2, y2 = (int(v) for v in obj.box)
        oc = ALERT_COLOR if obj.alert else OBJ_COLOR
        cv2.rectangle(img, (x1, y1), (x2, y2), oc, thick + (1 if obj.alert else 0))
        _label(img, f"{obj.label} {obj.conf:.2f}", (x1, y1), oc, 0.45 * scale)

    for p in persons:
        color = ALERT_COLOR if p.alert else SIDE_COLORS.get(p.side, SIDE_COLORS["unknown"])
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
        if p.alert:
            tag += " ALERT"
        _label(img, tag, (x1, y1), color, 0.55 * scale, max(1, int(scale)))
        if p.items:
            ic = ALERT_COLOR if p.alert else OBJ_COLOR
            _label(img, ", ".join(sorted(set(p.items)))[:60], (x1, y2 + int(22 * scale)), ic, 0.45 * scale)

    y = int(28 * scale)
    for line in hud:
        cv2.putText(img, ascii_text(line), (12, y), FONT, 0.7 * scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
        cv2.putText(img, ascii_text(line), (12, y), FONT, 0.7 * scale, (255, 255, 255), thick, cv2.LINE_AA)
        y += int(28 * scale)
    return img


def attach_objects(persons: list[Person], objects: list[WorldObject], margin: float = 0.15) -> None:
    """Assign each object to the smallest (expanded) person box containing the object's center."""
    for p in persons:
        p.items = []
        p.alert = False
    for obj in objects:
        cx = (obj.box[0] + obj.box[2]) / 2.0
        cy = (obj.box[1] + obj.box[3]) / 2.0
        best: Person | None = None
        best_area = float("inf")
        for p in persons:
            x1, y1, x2, y2 = p.box
            mw, mh = (x2 - x1) * margin, (y2 - y1) * margin
            if x1 - mw <= cx <= x2 + mw and y1 - mh <= cy <= y2 + mh:
                area = (x2 - x1) * (y2 - y1)
                if area < best_area:
                    best, best_area = p, area
        if best is not None:
            obj.owner = best.track_id
            best.items.append(obj.label)
            best.alert = best.alert or obj.alert
