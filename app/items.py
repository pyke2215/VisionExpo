"""Object tracking + temporal smoothing of open-vocabulary detections (scope = "person").

The detector runs on the whole frame and detects from scratch on every run, so raw results flicker
(confidence hovering around the threshold), swap between near-synonyms (hat / cap) or rival guesses
(hat / hair, knife / phone). Each run:

- boxes that overlap are rival guesses for the same region and are grouped; each group is one box for the
  per-camera object ByteTrack, so a region keeps its track id whatever it is read as;
- tracked regions are then given to the person whose (slightly enlarged) box covers them most; regions on
  nobody are tracked but never shown;
- each track is a slot: votes are summed per category, together with the votes of other slots of the same
  person covering the same region: the winner must lead the runner-up by `label_margin` (ambiguous regions
  stay hidden until one reading is clearly ahead), and a region won by a hidden category (hair, clothing)
  shows nothing;
- the winning category is shown after `confirm_hits` runs containing it in the last WINDOW runs
  (weapons: `alert_hits`) and hidden after `drop_misses` misses in a row;
- the label is the confidence-weighted vote inside that category, the box an EMA relative to the person;
- a track that lay still on nobody, or stays at a fixed image position while its owner walks away, is
  static scenery (bag on the floor or a table, coat on a rack) and is not shown until it moves again.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .objtrack import ObjectTracker
from .person import Person
from .world import WorldObject, _iou

WINDOW = 5  # runs remembered per slot
GROUP_IOU = 0.4  # boxes of one run overlapping this much are rival guesses for one region
RIVAL_IOU = 0.3  # slots of one person overlapping this much compete (a frequent "hair" slot outvotes a rare "hat")...
RIVAL_AREA = 0.5  # ...when similar in size (a shirt covering the whole torso does not compete with a backpack)
MATCH_IOU = 0.3  # a new track overlapping a slot not seen this run this much continues that slot
MERGE_IOU = 0.6  # two slots of one person this similar are the same item
BOX_ALPHA = 0.5  # weight of the newest box in the EMA
CONF_ALPHA = 0.4  # weight of the newest confidence in the per-label EMA
VOTE_DECAY = 0.85  # votes fade per hit so a corrected reading can win
LABEL_SWITCH = 1.3  # another label of the shown category needs this many times the votes to replace it
LABEL_MARGIN = 1.5  # default: winning category needs this many times the runner-up's votes
OWNER_GRACE_S = 2.0  # keep the owner this long while that person is not tracked
MIN_OWNER_COVER = 0.3  # fraction of the object inside the enlarged person box to belong to that person
STATIC_OWNER_MOVE = 0.15  # owner moved this fraction of its height since the slot appeared...
STATIC_OBJ_RATIO = 0.25  # ...while the object moved less than this fraction of that distance
IDLE_RUNS = 5  # tracked this many runs in a row on nobody without moving: scenery even if someone stands by later
IDLE_MOVE = 0.5  # "without moving": center stays within this fraction of the object's size


def _center(box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _rivals(a, b) -> bool:
    small, big = sorted((_area(a), _area(b)))
    return big > 0 and small / big >= RIVAL_AREA and _iou(a, b) >= RIVAL_IOU


def _rel(box, owner_box) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = owner_box
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    return (box[0] - x1) / w, (box[1] - y1) / h, (box[2] - x1) / w, (box[3] - y1) / h


def _best_owner(box, persons: list[Person], margin: float) -> Person | None:
    """Person whose enlarged box covers the largest fraction of `box`; ties go to the nearest box center."""
    area = max(1e-6, _area(box))
    cx, cy = _center(box)
    best, best_key = None, None
    for p in persons:
        x1, y1, x2, y2 = p.box
        mx, my = (x2 - x1) * margin, (y2 - y1) * margin * 0.5
        ex1, ey1, ex2, ey2 = x1 - mx, y1 - my, x2 + mx, y2 + my
        inter = max(0.0, min(box[2], ex2) - max(box[0], ex1)) * max(0.0, min(box[3], ey2) - max(box[1], ey1))
        cover = inter / area
        if cover < MIN_OWNER_COVER:
            continue
        dist = ((cx - (x1 + x2) / 2.0) / max(1.0, x2 - x1)) ** 2 + ((cy - (y1 + y2) / 2.0) / max(1.0, y2 - y1)) ** 2
        key = (round(cover, 2), -dist)
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best


def _group(dets: list[WorldObject]) -> list[list[WorldObject]]:
    """Cluster one run's boxes into regions; each group is sorted by confidence."""
    groups: list[list[WorldObject]] = []
    for d in sorted(dets, key=lambda o: o.conf, reverse=True):
        g = next((g for g in groups if _iou(g[0].box, d.box) >= GROUP_IOU and _rivals(g[0].box, d.box)), None)
        if g is None:
            groups.append([d])
        else:
            g.append(d)
    return groups


def _lead(group: list[WorldObject]) -> WorldObject:
    return next((o for o in group if not o.hidden), group[0])


@dataclass
class Slot:
    tids: set[int]  # object track ids (several after merging duplicates)
    box: tuple[float, float, float, float]  # last detected box in image coordinates
    owner: int | None = None
    rel: tuple[float, float, float, float] | None = None  # box relative to the owner's box (0..1 of its w/h)
    anchor_obj: tuple[float, float] | None = None
    anchor_owner: tuple[float, float] | None = None
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
    idle_anchor: tuple[float, float] | None = None
    idle_runs: int = 0
    clip: float | None = None  # last CLIP score of the leading detection (when clip_verify is on)

    def hit(self, group: list[WorldObject], owner: Person | None, now: float) -> None:
        self.votes = {k: v * VOTE_DECAY for k, v in self.votes.items() if v * VOTE_DECAY > 0.01}
        for o in group:
            self.votes[o.label] = self.votes.get(o.label, 0.0) + o.conf
            self.meta[o.label] = (o.category, o.hidden, o.alert)
            prev = self.confs.get(o.label)
            self.confs[o.label] = o.conf if prev is None else (1 - CONF_ALPHA) * prev + CONF_ALPHA * o.conf
        lead = _lead(group)
        self.box = lead.box
        if lead.clip is not None:
            self.clip = lead.clip if self.clip is None else 0.5 * self.clip + 0.5 * lead.clip
        self.hist.append(frozenset(o.category for o in group))
        self.hits += 1
        self.misses = 0
        if owner is not None:
            self.idle_anchor, self.idle_runs = None, 0
            self._follow(owner, now)
            return
        if self.owner is not None and now - self.owner_seen > OWNER_GRACE_S:
            self.owner = None
            self.rel = None
        if self.owner is None:
            self._check_idle()

    def _check_idle(self) -> None:
        c = _center(self.box)
        size = max(1.0, self.box[2] - self.box[0], self.box[3] - self.box[1])
        if self.idle_anchor is None or _dist(c, self.idle_anchor) > IDLE_MOVE * size:
            self.idle_anchor, self.idle_runs = c, 0
        self.idle_runs += 1
        if self.idle_runs >= IDLE_RUNS:
            self.static = True

    def _follow(self, owner: Person, now: float) -> None:
        rel = _rel(self.box, owner.box)
        if owner.track_id != self.owner or self.rel is None:
            self.owner = owner.track_id
            self.rel = rel
            self.anchor_obj = _center(self.box)
            self.anchor_owner = _center(owner.box)
        else:
            self.rel = tuple(BOX_ALPHA * n + (1 - BOX_ALPHA) * r for n, r in zip(rel, self.rel))
            self._check_static(owner.box)
        self.owner_seen = now

    def miss(self) -> None:
        self.hist.append(frozenset())
        self.misses += 1

    def absorb(self, other: "Slot") -> None:
        self.tids |= other.tids
        for label, v in other.votes.items():
            self.votes[label] = self.votes.get(label, 0.0) + v
        for label, c in other.confs.items():
            self.confs[label] = max(c, self.confs.get(label, 0.0))
        self.meta.update({k: v for k, v in other.meta.items() if k not in self.meta})
        self.static = self.static or other.static
        a, b = list(self.hist), list(other.hist)
        n = max(len(a), len(b))
        a, b = [frozenset()] * (n - len(a)) + a, [frozenset()] * (n - len(b)) + b
        self.hist = deque((x | y for x, y in zip(a, b)), maxlen=WINDOW)
        if other.misses < self.misses:
            # The restarted track is the one seen most recently: continue from its position. The anchors stay,
            # so a static item that was carried away is noticed as moved.
            self.box, self.rel, self.misses = other.box, other.rel, other.misses
            self.owner, self.owner_seen = other.owner, max(self.owner_seen, other.owner_seen)
            if other.clip is not None:
                self.clip = other.clip

    def _check_static(self, owner_box) -> None:
        owner_h = max(1.0, owner_box[3] - owner_box[1])
        obj_move = _dist(_center(self.box), self.anchor_obj)
        if self.static:
            # Picked up: the tracked object itself moved, it is no longer scenery.
            if obj_move >= STATIC_OWNER_MOVE * owner_h:
                self.static = False
                self.anchor_obj = _center(self.box)
                self.anchor_owner = _center(owner_box)
            return
        owner_move = _dist(_center(owner_box), self.anchor_owner)
        if owner_move < STATIC_OWNER_MOVE * owner_h:
            return
        if obj_move < STATIC_OBJ_RATIO * owner_move:
            self.static = True
        else:
            # Moved together with the owner: restart the check from here.
            self.anchor_obj = _center(self.box)
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
        self.tracker = ObjectTracker()

    def clear(self) -> None:
        self.slots = []
        self.tracker.reset()

    def update(self, detections: list[WorldObject], persons: list[Person], cfg: dict) -> None:
        """One detector run on the whole frame."""
        confirm_hits = int(cfg.get("confirm_hits", 2))
        alert_hits = int(cfg.get("alert_hits", 3))
        drop_misses = int(cfg.get("drop_misses", 3))
        reject_static = bool(cfg.get("reject_static", True))
        margin = max(1.0, float(cfg.get("label_margin", LABEL_MARGIN)))
        person_margin = float(cfg.get("person_margin", 0.1))
        min_person_px = float(cfg.get("min_person_px", 60))
        owners = [p for p in persons if p.track_id is not None and (p.box[3] - p.box[1]) >= min_person_px]
        now = time.time()

        groups = _group(detections)
        ids = self.tracker.update([_lead(g).box for g in groups], [g[0].conf for g in groups])
        by_tid = {t: s for s in self.slots for t in s.tids}
        hit: set[int] = set()
        for g, tid in zip(groups, ids):
            if tid is None:
                continue
            box = _lead(g).box
            owner = _best_owner(box, owners, person_margin)
            s = by_tid.get(tid)
            if s is None:
                s = self._reclaim(box, owner, hit)
                if s is None:
                    s = Slot(tids=set(), box=box)
                    self.slots.append(s)
                s.tids.add(tid)
                by_tid[tid] = s
            elif id(s) in hit:
                continue
            s.hit(g, owner, now)
            hit.add(id(s))
        for s in self.slots:
            if id(s) not in hit:
                s.miss()

        self._merge_duplicates()
        rivals = self._rival_votes()
        kept = []
        for s in self.slots:
            s.decide(confirm_hits, alert_hits, drop_misses, margin, reject_static, rivals.get(id(s), {}))
            if s.shown or s.misses < WINDOW:
                kept.append(s)
        self.slots = kept

    def _reclaim(self, box, owner: Person | None, hit: set[int]) -> Slot | None:
        """Slot not seen this run at the same place (relative to the same person, or in the image) as a new track.

        ByteTrack loses small objects that start moving fast (a bag picked up and carried); the new track id
        continues the old slot so its votes and static state carry over.
        """
        rel = _rel(box, owner.box) if owner is not None else None
        best, best_iou = None, MATCH_IOU
        for s in self.slots:
            if id(s) in hit:
                continue
            iou = _iou(s.box, box)
            if rel is not None and s.owner == owner.track_id and s.rel is not None:
                iou = max(iou, _iou(s.rel, rel))
            if iou >= best_iou:
                best, best_iou = s, iou
        return best

    def _rival_votes(self) -> dict[int, dict[str, float]]:
        """Per slot: category votes of the other slots of its owner that cover the same region."""
        out: dict[int, dict[str, float]] = {}
        for a in self.slots:
            if a.owner is None or a.rel is None:
                continue
            acc: dict[str, float] = {}
            for b in self.slots:
                if b is a or b.owner != a.owner or b.rel is None or not _rivals(a.rel, b.rel):
                    continue
                for cat, v in b.cat_votes().items():
                    acc[cat] = acc.get(cat, 0.0) + v
            out[id(a)] = acc
        return out

    def _merge_duplicates(self) -> None:
        """An object whose track was lost and restarted under a new id on the same person is one item."""
        self.slots.sort(key=lambda s: (s.hits, sum(s.votes.values())), reverse=True)
        out: list[Slot] = []
        for s in self.slots:
            twin = None
            if s.owner is not None and s.rel is not None:
                twin = next((k for k in out if k.owner == s.owner and k.rel is not None
                             and _iou(k.rel, s.rel) >= MERGE_IOU), None)
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
            if p is None or s.rel is None or not s.shown or conf < show_conf:
                continue
            x1, y1, x2, y2 = p.box
            pw, ph = x2 - x1, y2 - y1
            box = (x1 + s.rel[0] * pw, y1 + s.rel[1] * ph, x1 + s.rel[2] * pw, y1 + s.rel[3] * ph)
            out.append(WorldObject(label=s.label, conf=conf, box=box, alert=s.alert, owner=s.owner, rel=s.rel,
                                   category=s.category, clip=s.clip))
            if s.label not in p.items:
                p.items.append(s.label)
            p.alert = p.alert or s.alert
        return out
