"""Default open-vocabulary classes: accessories a person carries / wears (headwear, eyewear, masks, bags,
devices) and weapons.

One class per kind of object ("hat" also catches caps and beanies): every extra near-synonym class is one
more way for a region to match the wrong word. A class can list several `prompts` whose text embeddings are
averaged, but measured on real frames the single most common noun usually scores best ("cell phone" alone
found more phones than with "smartphone" / "mobile phone"; "glasses" alone found glasses, averaged with
"eyeglasses" / "sunglasses" it found none), so only add a prompt after checking it helps.
Each entry is (class label, vietnamese display label, prompts).
Categories listed in ALERT_CATEGORIES are drawn in red and reported as alerts.
Categories listed in SUPPRESS_CATEGORIES are detected but never shown: a confident "jacket" box on the torso
absorbs weaker look-alike guesses there ("backpack", "lanyard"); "hair" does the same for "hat" / "helmet"
on bare heads seen from above.
"""

from __future__ import annotations

import json
from pathlib import Path

VOCABULARY_VERSION = 3

DEFAULT_VOCABULARY: dict[str, list[tuple[str, str, list[str]]]] = {
    "headwear": [
        ("hat", "nón / mũ", ["hat"]),
        ("helmet", "mũ bảo hiểm", ["helmet"]),
    ],
    "face": [
        ("glasses", "kính", ["glasses"]),
        ("face mask", "khẩu trang", ["face mask"]),
    ],
    "bags": [
        ("backpack", "ba lô", ["backpack"]),
        ("handbag", "túi xách", ["handbag"]),
        ("shopping bag", "túi mua sắm", ["shopping bag"]),
        ("suitcase", "vali", ["suitcase"]),
    ],
    "accessories": [
        ("watch", "đồng hồ", ["watch"]),
        ("headphones", "tai nghe", ["headphones", "headset"]),
        ("lanyard", "thẻ đeo", ["lanyard"]),
    ],
    "devices": [
        ("cell phone", "điện thoại", ["cell phone"]),
    ],
    "weapons": [
        ("knife", "dao", ["knife"]),
        ("gun", "súng", ["gun"]),
        ("baseball bat", "gậy", ["baseball bat"]),
        ("axe", "rìu", ["axe"]),
        ("scissors", "kéo", ["scissors"]),
    ],
    "clothing": [
        ("jacket", "áo khoác", ["jacket"]),
        ("shirt", "áo", ["shirt"]),
        ("trousers", "quần", ["trousers"]),
        ("shoes", "giày", ["shoes"]),
        ("hair", "tóc", ["hair"]),
    ],
}

CATEGORY_LABELS: dict[str, str] = {
    "headwear": "Nón & mũ",
    "face": "Kính & khẩu trang",
    "bags": "Túi",
    "accessories": "Phụ kiện",
    "devices": "Điện thoại",
    "weapons": "Vũ khí (cảnh báo)",
    "clothing": "Quần áo & tóc – chỉ để lọc nhận nhầm, không hiển thị",
    "custom": "Tự thêm",
}

ALERT_CATEGORIES = {"weapons"}
SUPPRESS_CATEGORIES = {"clothing"}


def default_vocabulary_doc() -> dict:
    return {
        "version": VOCABULARY_VERSION,
        "categories": [
            {
                "id": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "alert": cat in ALERT_CATEGORIES,
                "suppress": cat in SUPPRESS_CATEGORIES,
                "items": [{"en": en, "vi": vi, "prompts": prompts} for en, vi, prompts in items],
            }
            for cat, items in DEFAULT_VOCABULARY.items()
        ],
    }


def _migrate(doc: dict) -> dict:
    """Older versions had one class per synonym: switch to the current defaults, keep the words the user added."""
    new = default_vocabulary_doc()
    custom = next((c for c in doc.get("categories") or [] if c.get("id") == "custom" and c.get("items")), None)
    if custom:
        new["categories"].append(custom)
    return new


def load_vocabulary(path: Path) -> dict:
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("categories"), list):
                if int(doc.get("version") or 1) < VOCABULARY_VERSION:
                    doc = _migrate(doc)
                    save_vocabulary(path, doc)
                return doc
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    doc = default_vocabulary_doc()
    save_vocabulary(path, doc)
    return doc


def save_vocabulary(path: Path, doc: dict) -> None:
    doc = {"version": VOCABULARY_VERSION, **{k: v for k, v in doc.items() if k != "version"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def _items(doc: dict):
    for cat in doc.get("categories") or []:
        for item in cat.get("items") or []:
            en = str(item.get("en") or "").strip().lower()
            if en:
                yield cat, item, en


def flatten_classes(doc: dict) -> list[str]:
    """Ordered unique class labels; index = class id inside the baked model."""
    seen: set[str] = set()
    out: list[str] = []
    for _, _, en in _items(doc):
        if en not in seen:
            seen.add(en)
            out.append(en)
    return out


def class_prompts(doc: dict) -> dict[str, list[str]]:
    """class label -> prompts whose text embeddings are averaged into it (default: the label itself)."""
    out: dict[str, list[str]] = {}
    for _, item, en in _items(doc):
        if en in out:
            continue
        prompts = [str(p).strip().lower() for p in item.get("prompts") or [] if str(p).strip()]
        out[en] = list(dict.fromkeys(prompts)) or [en]
    return out


def vi_labels(doc: dict) -> dict[str, str]:
    return {en: str(item.get("vi") or en) for _, item, en in _items(doc)}


def label_categories(doc: dict) -> dict[str, str]:
    """class label -> category id (first category wins for duplicates)."""
    out: dict[str, str] = {}
    for cat, _, en in _items(doc):
        out.setdefault(en, str(cat.get("id") or ""))
    return out


def _labels_where(doc: dict, flag: str, ids: set[str]) -> set[str]:
    return {en for cat, _, en in _items(doc) if cat.get(flag) or cat.get("id") in ids}


def alert_labels(doc: dict) -> set[str]:
    return _labels_where(doc, "alert", ALERT_CATEGORIES)


def suppress_labels(doc: dict) -> set[str]:
    """Labels detected only to absorb look-alike false positives; never shown or reported."""
    return _labels_where(doc, "suppress", SUPPRESS_CATEGORIES)
