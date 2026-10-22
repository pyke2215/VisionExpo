"""Canonical paths inside the Exhibition folder."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
WEB_DIR = ROOT / "web"
CONFIG_DIR = ROOT / "config"
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
DATABASE_FILE = DATA_DIR / "exhibition.sqlite3"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
VOCABULARY_FILE = CONFIG_DIR / "vocabulary.json"

PERSON_MODEL_DEFAULT = "yolo26n-pose.pt"
WORLD_BASE_MODEL = "yoloe-26l-seg.pt"  # open-vocabulary detector; a "-world" model (yolov8s-worldv2.pt) also works
WORLD_BAKED_MODEL = "yoloe_exhibition.pt"
PPE_MODEL_DEFAULT = "yolo_ppe.pt"  # Hardhat / Gloves (+ NO-* negatives)
MASK_MODEL_DEFAULT = "yolo26n-seg.pt"  # person masks for the glove check (pixels of the person only)


def resolve_model(name: str) -> Path | str:
    """Local file under models/ if present, otherwise the bare name (Ultralytics auto-download)."""
    p = Path(name)
    if p.is_absolute() and p.is_file():
        return p
    local = MODELS_DIR / p.name
    if local.is_file():
        return local
    rel = ROOT / p
    if rel.is_file():
        return rel
    return name
