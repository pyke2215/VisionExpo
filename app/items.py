"""Temporal smoothing of YOLO-World objects per person (scope = "person").

YOLO-World detects from scratch on every run, so raw results flicker (confidence hovering around the
threshold), swap between near-synonyms (hat / cap) or rival guesses (hat / hair, knife / phone) and jump
with the person crop. Each person keeps slots (one physical region each), matched across runs in
person-relative coordinates:

- boxes of one run that overlap are rival guesses for the same region and all vote into one slot;
- votes are summed per category, together with the votes of other slots of the person covering the same
  region: the winner must lead the runner-up by `label_margin` (ambiguous regions stay hidden until one
  reading is clearly ahead), and a region won by a hidden category (hair, clothing) shows nothing;
- the winning category is shown after `confirm_hits` runs containing it in the last WINDOW runs
  (weapons: `alert_hits`) and hidden after `drop_misses` misses in a row;
- the label is the confidence-weighted vote inside that category, the box an EMA relative to the person;
- a slot that stays at a fixed image position while its owner walks away is static scenery (bag on a
  table, coat on a rack) and is never shown.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .person import Person
from .world import WorldObject, _iou

WINDOW = 5  # runs remembered per slot
GROUP_IOU = 0.4  # boxes of one run overlapping this much are rival guesses for one region
RIVAL_IOU = 0.3  # slots of one person overlapping this much compete (a frequent "hair" slot outvotes a rare "hat")...
RIVAL_AREA = 0.5  # ...when similar in size (a shirt covering the whole torso does not compete with a backpack)
MATCH_IOU = 0.3  # person-relative IoU to continue a slot
MERGE_IOU = 0.6  # two slots of one person this similar are the same item
BOX_ALPHA = 0.5  # weight of the newest box in the EMA
CONF_ALPHA = 0.4  # weight of the newest confidence in the per-label EMA
VOTE_DECAY = 0.85  # votes fade per hit so a corrected reading can win
LABEL_SWITCH = 1.3  # another label of the shown category needs this many times the votes to replace it
LABEL_MARGIN = 1.5  # default: winning category needs this many times the runner-up's votes
OWNER_GRACE_S = 2.0  # keep slots this long while their owner is not tracked
STATIC_OWNER_MOVE = 0.15  # owner moved this fraction of its height since the slot appeared...
STATIC_OBJ_RATIO = 0.25  # ...while the object moved less than this fraction of that distance


def _center(box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _rivals(a, b) -> bool:
    small, big = sorted((_area(a), _area(b)))
    return big > 0 and small / big >= RIVAL_AREA and _iou(a, b) >= RIVAL_IOU


def _group(dets: list[WorldObject]) -> list[list[WorldObject]]:
    """Cluster one run's boxes of a person into regions; each group is sorted by confidence."""
    groups: list[list[WorldObject]] = []
    for d in sorted(dets, key=lambda o: o.conf, reverse=True):
        g = next((g for g in groups if _iou(g[0].box, d.box) >= GROUP_IOU and _rivals(g[0].box, d.box)), None)
        if g is None:
            groups.append([d])
        else:
            g.append(d)
    return groups


@dataclass
class Slot:
    owner: int
    rel: tuple[float, float, float, float]
    box: tuple[float, float, float, float]  # last detected box in image coordinates
    anchor_obj: tuple[float, float]
    anchor_owner: tuple[float, float]
    label: str = ""
    category: str = ""
    alert: bool = False
    votes: dict[str, float] = field(default_factory=dict)
    confs: dict[str, float] = field(default_factory=dict)
    meta: dict[str, tuple[str, bool, bool]] = field(default_factory=dict)  # label -> (category, hidden, alert)
    hist: deque = field(default_factory=lambda: deque(maxlen=WINDOW))  # categories seen per run
    hits: int = 0
    misses: int = 0
    shown: bool = False
    static: bool = False
    owner_seen: float = field(default_factory=time.time)

    def hit(self, group: list[WorldObject], owner_box) -> None:
        self.votes = {k: v * VOTE_DECAY for k, v in self.votes.items() if v * VOTE_DECAY > 0.01}
        for o in group:
            self.votes[o.label] = self.votes.get(o.label, 0.0) + o.conf
            self.meta[o.label] = (o.category, o.hidden, o.alert)
            prev = self.confs.get(o.label)
            self.confs[o.label] = o.conf if prev is None else (1 - CONF_ALPHA) * prev + CONF_ALPHA * o.conf
        lead = next((o for o in group if not o.hidden), group[0])
        self.box = lead.box
        if self.hits:
            self.rel = tuple(BOX_ALPHA * n + (1 - BOX_ALPHA) * r for n, r in zip(lead.rel, self.rel))
        self.hist.append(frozenset(o.category for o in group))
        self.hits += 1
        self.misses = 0
        self._check_static(lead.box, owner_box)

    def miss(self) -> None:
        self.hist.append(frozenset())
        self.misses += 1

    def absorb(self, other: "Slot") -> None:
        for label, v in other.votes.items():
            self.votes[label] = self.votes.get(label, 0.0) + v
        for label, c in other.confs.items():
            self.confs[label] = max(c, self.confs.get(label, 0.0))
        self.meta.update({k: v for k, v in other.meta.items() if k not in self.meta})
        self.static = self.static or other.static

    def _check_static(self, obj_box, owner_box) -> None:
        if self.static:
            return
        owner_h = max(1.0, owner_box[3] - owner_box[1])
        owner_move = _dist(_center(owner_box), self.anchor_owner)
        if owner_move < STATIC_OWNER_MOVE * owner_h:
            return
        if _dist(_center(obj_box), self.anchor_obj) < STATIC_OBJ_RATIO * owner_move:
            self.static = True
        else:
            # Moved together with the owner: restart the check from here.
            self.anchor_obj = _center(obj_box)
            self.anchor_owner = _center(owner_box)

    def cat_votes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for label, v in self.votes.items():
            cat = self.meta[label][0]
            out[cat] = out.get(cat, 0.0) + v
        return out

    def decide(self, confirm_hits: int, alert_hits: int, drop_misses: int, margin: float, reject_static: bool,
               rivals: dict[str, float]) -> None:
        if not self.votes or self.misses >= drop_misses or (self.static and reject_static):
            self.shown = False
            return
        own = self.cat_votes()
        ranked = sorted(own.items(), key=lambda kv: kv[1], reverse=True)
        cat, best = ranked[0]
        # Runner-up: this slot's other readings or overlapping slots of the same person read differently.
        others = {k: v for k, v in own.items() if k != cat}
        for k, v in rivals.items():
            if k != cat:
                others[k] = others.get(k, 0.0) + v
        second = max(others.values(), default=0.0)
        keeping = self.shown and cat == self.category
        # Already shown: a smaller lead keeps it, so the box does not blink around the margin.
        need_ratio = 1.0 + (margin - 1.0) / 2.0 if keeping else margin
        labels = [lb for lb in self.votes if self.meta[lb][0] == cat]
        hidden = any(self.meta[lb][1] for lb in labels)
        alert = any(self.meta[lb][2] for lb in labels)
        if hidden or best < need_ratio * second:
            self.shown = False
            return
        if not keeping:
            seen = sum(1 for cats in self.hist if cat in cats)
            if seen < (max(confirm_hits, alert_hits) if alert else confirm_hits):
                self.shown = False
                return
        cat_votes = {lb: self.votes[lb] for lb in labels}
        top = max(cat_votes, key=cat_votes.get)
        if not keeping or self.label not in cat_votes or cat_votes[top] > LABEL_SWITCH * cat_votes[self.label]:
            self.label = top
        self.category = cat
        self.alert = alert
        self.shown = True


class ItemTracker:
    def __init__(self) -> None:
        self.slots: list[Slot] = []

    def clear(self) -> None:
        self.slots = []

    def update(self, detections: list[WorldObject], owners: set[int], persons: list[Person], cfg: dict) -> None:
        """One YOLO-World run: `owners` are the track ids whose crops were actually examined."""
        by_tid = {p.track_id: p for p in persons if p.track_id is not None}
        confirm_hits = int(cfg.get("confirm_hits", 2))
        alert_hits = int(cfg.get("alert_hits", 3))
        drop_misses = int(cfg.get("drop_misses", 3))
        reject_static = bool(cfg.get("reject_static", True))
        margin = max(1.0, float(cfg.get("label_margin", LABEL_MARGIN)))

        dets_by_owner: dict[int, list[WorldObject]] = {}
        for d in detections:
            if d.owner in by_tid and d.rel is not None:
                dets_by_owner.setdefault(d.owner, []).append(d)

        for tid in owners:
            owner_box = by_tid[tid].box if tid in by_tid else None
            if owner_box is None:
                continue
            slots = [s for s in self.slots if s.owner == tid]
            groups = _group(dets_by_owner.get(tid, []))
            pairs = []
            for si, s in enumerate(slots):
                for gi, g in enumerate(groups):
                    # Relative match follows carried items, absolute match keeps static scenery in one slot.
                    iou = max(max(_iou(s.rel, d.rel), _iou(s.box, d.box)) for d in g)
                    if iou >= MATCH_IOU:
                        known = any(d.label in s.votes for d in g)
                        pairs.append((iou + (0.1 if known else 0.0), si, gi))
            pairs.sort(reverse=True)
            used_s, used_g = set(), set()
            for _, si, gi in pairs:
                if si in used_s or gi in used_g:
                    continue
                used_s.add(si)
                used_g.add(gi)
                slots[si].hit(groups[gi], owner_box)
            for si, s in enumerate(slots):
                if si not in used_s:
                    s.miss()
            for gi, g in enumerate(groups):
                if gi in used_g:
                    continue
                lead = next((o for o in g if not o.hidden), g[0])
                s = Slot(owner=tid, rel=lead.rel, box=lead.box,
                         anchor_obj=_center(lead.box), anchor_owner=_center(owner_box))
                s.hit(g, owner_box)
                self.slots.append(s)

        self._merge_duplicates()
        rivals = self._rival_votes()
        now = time.time()
        kept = []
        for s in self.slots:
            if s.owner in by_tid:
                s.owner_seen = now
            elif now - s.owner_seen > OWNER_GRACE_S:
                continue
            s.decide(confirm_hits, alert_hits, drop_misses, margin, reject_static, rivals.get(id(s), {}))
            if s.shown or s.misses < WINDOW:
                kept.append(s)
        self.slots = kept

    def _rival_votes(self) -> dict[int, dict[str, float]]:
        """Per slot: category votes of the other slots of its owner that cover the same region."""
        out: dict[int, dict[str, float]] = {}
        for a in self.slots:
            acc: dict[str, float] = {}
            for b in self.slots:
                if b is a or b.owner != a.owner or not _rivals(a.rel, b.rel):
                    continue
                for cat, v in b.cat_votes().items():
                    acc[cat] = acc.get(cat, 0.0) + v
            out[id(a)] = acc
        return out

    def _merge_duplicates(self) -> None:
        self.slots.sort(key=lambda s: (s.hits, sum(s.votes.values())), reverse=True)
        out: list[Slot] = []
        for s in self.slots:
            twin = next((k for k in out if k.owner == s.owner and _iou(k.rel, s.rel) >= MERGE_IOU), None)
            if twin is None:
                out.append(s)
            else:
                twin.absorb(s)
        self.slots = out

    def render(self, persons: list[Person], show_conf: float = 0.0) -> list[WorldObject]:
        """Objects to draw for this frame; sets `items` / `alert` on the persons."""
        by_tid = {p.track_id: p for p in persons if p.track_id is not None}
        for p in persons:
            p.items = []
            p.alert = False
        out: list[WorldObject] = []
        for s in self.slots:
            p = by_tid.get(s.owner)
            conf = s.confs.get(s.label, 0.0)
            if p is None or not s.shown or conf < show_conf:
                continue
            x1, y1, x2, y2 = p.box
            pw, ph = x2 - x1, y2 - y1
            box = (x1 + s.rel[0] * pw, y1 + s.rel[1] * ph, x1 + s.rel[2] * pw, y1 + s.rel[3] * ph)
            out.append(WorldObject(label=s.label, conf=conf, box=box, alert=s.alert, owner=s.owner, rel=s.rel,
                                   category=s.category))
            if s.label not in p.items:
                p.items.append(s.label)
            p.alert = p.alert or s.alert
        return out
