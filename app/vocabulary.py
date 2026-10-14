"""Default YOLO-World vocabulary: accessories a person carries / wears (headwear, eyewear, masks, bags,
jewellery) and weapons.

Each entry is (english prompt for the model, vietnamese display label).
Prompts stay short and concrete; CLIP text embeddings work best with simple nouns.
Categories listed in ALERT_CATEGORIES are drawn in red and reported as alerts.
Categories listed in SUPPRESS_CATEGORIES are detected but never shown: a confident "jacket" box on the torso
absorbs weaker look-alike guesses there ("backpack", "id badge") before it is dropped; "hair" does the same
for "helmet" / "beanie" on bare heads seen from above.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_VOCABULARY: dict[str, list[tuple[str, str]]] = {
    "headwear": [
        ("hat", "mũ"),
        ("cap", "mũ lưỡi trai"),
        ("beanie", "mũ len"),
        ("bucket hat", "mũ tai bèo"),
        ("sun hat", "mũ rộng vành"),
        ("conical hat", "nón lá"),
        ("helmet", "mũ bảo hiểm"),
        ("hard hat", "mũ bảo hộ"),
        ("headscarf", "khăn trùm đầu"),
        ("headband", "băng đô"),
    ],
    "face": [
        ("glasses", "kính mắt"),
        ("sunglasses", "kính râm"),
        ("goggles", "kính bảo hộ"),
        ("face mask", "khẩu trang"),
        ("face shield", "tấm chắn mặt"),
        ("balaclava", "mũ trùm kín mặt"),
    ],
    "bags": [
        ("backpack", "ba lô"),
        ("handbag", "túi xách"),
        ("shoulder bag", "túi đeo vai"),
        ("crossbody bag", "túi đeo chéo"),
        ("waist bag", "túi đeo hông"),
        ("tote bag", "túi tote"),
        ("shopping bag", "túi mua sắm"),
        ("plastic bag", "túi nilon"),
        ("briefcase", "cặp tài liệu"),
        ("suitcase", "vali"),
    ],
    "accessories": [
        ("watch", "đồng hồ"),
        ("smartwatch", "đồng hồ thông minh"),
        ("bracelet", "vòng tay"),
        ("necklace", "vòng cổ"),
        ("earrings", "bông tai"),
        ("tie", "cà vạt"),
        ("scarf", "khăn quàng"),
        ("belt", "thắt lưng"),
        ("gloves", "găng tay"),
        ("lanyard", "dây đeo thẻ"),
        ("id badge", "thẻ đeo"),
        ("headphones", "tai nghe"),
    ],
    "devices": [
        ("cell phone", "điện thoại"),
    ],
    "weapons": [
        ("knife", "dao"),
        ("kitchen knife", "dao bếp"),
        ("pocket knife", "dao gấp"),
        ("machete", "mã tấu"),
        ("sword", "kiếm"),
        ("axe", "rìu"),
        ("gun", "súng"),
        ("handgun", "súng ngắn"),
        ("rifle", "súng trường"),
        ("shotgun", "súng săn"),
        ("baseball bat", "gậy bóng chày"),
        ("crowbar", "xà beng"),
        ("baton", "dùi cui"),
        ("hammer", "búa"),
        ("scissors", "kéo"),
        ("taser", "súng điện"),
        ("slingshot", "ná cao su"),
    ],
    "clothing": [
        ("jacket", "áo khoác"),
        ("coat", "áo choàng"),
        ("hoodie", "áo hoodie"),
        ("sweater", "áo len"),
        ("shirt", "áo sơ mi"),
        ("t-shirt", "áo phông"),
        ("dress", "váy"),
        ("trousers", "quần dài"),
        ("jeans", "quần jean"),
        ("shorts", "quần short"),
        ("shoes", "giày"),
        ("hair", "tóc"),
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
        "categories": [
            {
                "id": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "alert": cat in ALERT_CATEGORIES,
                "suppress": cat in SUPPRESS_CATEGORIES,
                "items": [{"en": en, "vi": vi} for en, vi in items],
            }
            for cat, items in DEFAULT_VOCABULARY.items()
        ]
    }


def load_vocabulary(path: Path) -> dict:
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("categories"), list):
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    doc = default_vocabulary_doc()
    save_vocabulary(path, doc)
    return doc


def save_vocabulary(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def flatten_classes(doc: dict) -> list[str]:
    """Ordered unique english prompts; index = class id inside the baked YOLO-World model."""
    seen: set[str] = set()
    out: list[str] = []
    for cat in doc.get("categories") or []:
        for item in cat.get("items") or []:
            en = str(item.get("en") or "").strip().lower()
            if en and en not in seen:
                seen.add(en)
                out.append(en)
    return out


def vi_labels(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for cat in doc.get("categories") or []:
        for item in cat.get("items") or []:
            en = str(item.get("en") or "").strip().lower()
            if en:
                out[en] = str(item.get("vi") or en)
    return out


def label_categories(doc: dict) -> dict[str, str]:
    """english prompt -> category id (first category wins for duplicates)."""
    out: dict[str, str] = {}
    for cat in doc.get("categories") or []:
        for item in cat.get("items") or []:
            en = str(item.get("en") or "").strip().lower()
            if en:
                out.setdefault(en, str(cat.get("id") or ""))
    return out


def _labels_where(doc: dict, flag: str, ids: set[str]) -> set[str]:
    out: set[str] = set()
    for cat in doc.get("categories") or []:
        if cat.get(flag) or cat.get("id") in ids:
            for item in cat.get("items") or []:
                en = str(item.get("en") or "").strip().lower()
                if en:
                    out.add(en)
    return out


def alert_labels(doc: dict) -> set[str]:
    return _labels_where(doc, "alert", ALERT_CATEGORIES)


def suppress_labels(doc: dict) -> set[str]:
    """Labels detected only to absorb look-alike false positives; never shown or reported."""
    return _labels_where(doc, "suppress", SUPPRESS_CATEGORIES)
