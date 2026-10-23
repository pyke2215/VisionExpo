"""Download / copy the pose and person-mask weights into models/ so the app runs offline.

Run once on a machine with internet:  python tools/prepare_models.py
yolo_ppe.pt is not downloadable: copy it into models/ by hand.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.paths import MASK_MODEL_DEFAULT, MODELS_DIR, PERSON_MODEL_DEFAULT, PPE_MODEL_DEFAULT  # noqa: E402

PRODUCTION_MODELS = ROOT.parent / "Production" / "models"


def ensure_person_model() -> None:
    target = MODELS_DIR / PERSON_MODEL_DEFAULT
    if target.is_file():
        print(f"[ok] {target.name} already present")
        return
    local = PRODUCTION_MODELS / PERSON_MODEL_DEFAULT
    if local.is_file():
        shutil.copy2(local, target)
        print(f"[ok] copied {local} -> {target}")
        return
    from ultralytics import YOLO

    YOLO(PERSON_MODEL_DEFAULT)  # downloads into cwd (= models/)
    print(f"[ok] downloaded {target.name}")


def ensure_mask_model() -> None:
    """yolo26n-seg.pt: person masks for the PPE glove check (the app runs without it, less accurately)."""
    target = MODELS_DIR / MASK_MODEL_DEFAULT
    if target.is_file():
        print(f"[ok] {target.name} already present")
        return
    from ultralytics import YOLO

    YOLO(MASK_MODEL_DEFAULT)  # downloads into cwd (= models/)
    print(f"[ok] downloaded {target.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="0")  # kept for old scripts; downloads do not need a device
    ap.add_argument("--skip-world", action="store_true", help=argparse.SUPPRESS)  # object detector removed
    args = ap.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(MODELS_DIR)
    ensure_person_model()
    ensure_mask_model()
    if not (MODELS_DIR / PPE_MODEL_DEFAULT).is_file():
        print(f"[!!] {PPE_MODEL_DEFAULT} missing: copy it into {MODELS_DIR} (PPE stays off without it)")
    print("[done] models ready in", MODELS_DIR)


if __name__ == "__main__":
    main()
