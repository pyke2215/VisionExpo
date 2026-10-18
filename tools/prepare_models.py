"""Download / copy all weights into models/ and bake the object vocabulary so the app runs offline.

Run once on a machine with internet:  python tools/prepare_models.py [--device 0|cpu] [--skip-world]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.paths import (  # noqa: E402
    MODELS_DIR,
    PERSON_MODEL_DEFAULT,
    VOCABULARY_FILE,
    WORLD_BAKED_MODEL,
    WORLD_BASE_MODEL,
)
from app.vocabulary import class_prompts, flatten_classes, load_vocabulary  # noqa: E402

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


def ensure_world(device: str) -> None:
    from app.world import _model_class, bake_vocabulary

    base = MODELS_DIR / WORLD_BASE_MODEL
    if not base.is_file():
        _model_class(WORLD_BASE_MODEL)(WORLD_BASE_MODEL)
        print(f"[ok] downloaded {base.name}")
    else:
        print(f"[ok] {base.name} already present")

    doc = load_vocabulary(VOCABULARY_FILE)
    classes = flatten_classes(doc)
    print(f"[..] encoding {len(classes)} classes (first run downloads the text encoder, MobileCLIP ~240 MB)")
    model = bake_vocabulary(classes, class_prompts(doc), device=device)
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    print(f"[ok] baked {len(names)} classes -> {MODELS_DIR / WORLD_BAKED_MODEL}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="0")
    ap.add_argument("--skip-world", action="store_true")
    args = ap.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(MODELS_DIR)
    ensure_person_model()
    if not args.skip_world:
        ensure_world(args.device)
    print("[done] models ready in", MODELS_DIR)


if __name__ == "__main__":
    main()
