"""SQLite storage: events (crossing, ppe_alert, camera, reset), per-person track summaries and periodic samples.

Column names predate the PPE-only build: tracks.items / tracks.alert and samples.objects / samples.alerts now hold
PPE labels and PPE alerts (rows from the object-detector era keep their old meaning).

A single writer thread owns the write connection (batched inserts, periodic sampling, retention purge);
reads open short-lived connections (WAL mode allows concurrent readers).
"""

from __future__ import annotations

import csv
import io
import json
import queue
import sqlite3
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    camera_id TEXT NOT NULL,
    type TEXT NOT NULL,
    track_id INTEGER,
    direction TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_cam_type ON events(camera_id, type, ts);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    camera_id TEXT NOT NULL,
    track_id INTEGER,
    first_ts REAL NOT NULL,
    last_ts REAL NOT NULL,
    duration REAL,
    frames INTEGER,
    items TEXT,
    alert INTEGER DEFAULT 0,
    first_side TEXT,
    last_side TEXT
);
CREATE INDEX IF NOT EXISTS idx_tracks_ts ON tracks(first_ts);
CREATE INDEX IF NOT EXISTS idx_tracks_cam ON tracks(camera_id, first_ts);

CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    camera_id TEXT NOT NULL,
    persons INTEGER,
    entries INTEGER,
    exits INTEGER,
    alerts INTEGER,
    objects TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS zone_samples (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    camera_id TEXT NOT NULL,
    zone_id TEXT NOT NULL,
    kind TEXT,
    name TEXT,
    inside INTEGER
);
CREATE INDEX IF NOT EXISTS idx_zone_samples_ts ON zone_samples(ts);

CREATE TABLE IF NOT EXISTS heat (
    hour_ts REAL NOT NULL,
    camera_id TEXT NOT NULL,
    cols INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    grid TEXT NOT NULL,
    PRIMARY KEY (hour_ts, camera_id)
);
"""
# Columns added after the first release: (table, column, type). Applied with ALTER TABLE on existing databases.
MIGRATIONS = (("tracks", "zones", "TEXT"), ("tracks", "visitor", "INTEGER"))

EVENT_COLUMNS = ("ts", "camera_id", "type", "track_id", "direction", "data")
TRACK_COLUMNS = (
    "camera_id", "track_id", "first_ts", "last_ts", "duration", "frames", "items", "alert", "first_side", "last_side",
    "zones", "visitor",
)
PURGE_EVERY_S = 3600.0
HEAT_FLUSH_S = 60.0  # how often workers' heat grids are added to the current hour row


def _local_offset_s() -> int:
    return -time.altzone if time.localtime().tm_isdst > 0 else -time.timezone


class Database:
    def __init__(
        self,
        path: Path,
        cfg_getter: Callable[[], dict],
        status_getter: Callable[[], dict] | None = None,
        heat_getter: Callable[[], dict] | None = None,
    ) -> None:
        self.path = path
        self._cfg = cfg_getter
        self._status = status_getter
        self._heat = heat_getter  # () -> {camera_id: grid taken from the worker (np.ndarray rows x cols)}
        self._q: queue.Queue = queue.Queue(maxsize=50000)
        self._stop = threading.Event()
        self.dropped = 0
        self.written = 0
        self.last_error: str | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            for table, col, typ in MIGRATIONS:
                if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self._thread = threading.Thread(target=self._writer, name="db-writer", daemon=True)

    # ---------- lifecycle ----------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg().get("enabled", True))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ---------- ingest ----------
    def on_event(self, event: dict) -> None:
        if not self.enabled:
            return
        if event.get("type") == "track" and not self._cfg().get("save_tracks", True):
            return
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def _writer(self) -> None:
        conn = self._connect()
        next_sample = time.time() + 5.0
        next_purge = time.time() + 60.0
        next_heat = time.time() + HEAT_FLUSH_S
        while not self._stop.is_set() or not self._q.empty():
            batch: list[dict] = []
            try:
                batch.append(self._q.get(timeout=0.5))
                while len(batch) < 500:
                    batch.append(self._q.get_nowait())
            except queue.Empty:
                pass
            try:
                if batch:
                    self._insert(conn, batch)
                now = time.time()
                if now >= next_sample:
                    every = max(2.0, float(self._cfg().get("sample_every_s", 10)))
                    next_sample = now + every
                    if self.enabled and self._status is not None:
                        self._sample(conn, now)
                if now >= next_heat:  # the final flush on stop happens after the loop
                    next_heat = now + HEAT_FLUSH_S
                    self._flush_heat(conn, now)
                if now >= next_purge:
                    next_purge = now + PURGE_EVERY_S
                    self._purge(conn, now)
                self.last_error = None
            except sqlite3.Error as exc:
                self.last_error = str(exc)
                print(f"[db] {exc}")
                time.sleep(1.0)
        try:
            self._flush_heat(conn, time.time())
        except sqlite3.Error as exc:
            print(f"[db] heat flush on stop: {exc}")
        conn.close()

    def _insert(self, conn: sqlite3.Connection, batch: list[dict]) -> None:
        ev_rows, tr_rows = [], []
        for ev in batch:
            if ev.get("type") == "track":
                tr_rows.append((
                    ev["camera_id"], ev.get("track_id"), ev["first_ts"], ev["last_ts"], ev.get("duration"),
                    ev.get("frames"), json.dumps(ev.get("items") or {}, ensure_ascii=False),
                    1 if ev.get("alert") else 0, ev.get("first_side"), ev.get("last_side"),
                    json.dumps(ev.get("zones") or {}, ensure_ascii=False),
                    None if ev.get("visitor") is None else int(bool(ev.get("visitor"))),
                ))
            else:
                extra = {k: v for k, v in ev.items() if k not in ("ts", "camera_id", "type", "track_id", "direction")}
                ev_rows.append((
                    ev["ts"], ev["camera_id"], ev["type"], ev.get("track_id"), ev.get("direction"),
                    json.dumps(extra, ensure_ascii=False) if extra else None,
                ))
        with conn:
            if ev_rows:
                conn.executemany(f"INSERT INTO events ({','.join(EVENT_COLUMNS)}) VALUES (?,?,?,?,?,?)", ev_rows)
            if tr_rows:
                conn.executemany(
                    f"INSERT INTO tracks ({','.join(TRACK_COLUMNS)}) VALUES ({','.join('?' * len(TRACK_COLUMNS))})",
                    tr_rows,
                )
        self.written += len(batch)

    def _sample(self, conn: sqlite3.Connection, now: float) -> None:
        status = self._status()
        rows, zone_rows = [], []
        for cam in status.get("cameras") or []:
            if cam.get("status") != "running":
                continue
            cr = cam.get("crossing") or {}
            rows.append((
                now, cam["id"], cam.get("persons", 0), cr.get("entries", 0), cr.get("exits", 0),
                cam.get("ppe_alerts", 0), json.dumps(cam.get("ppe") or {}, ensure_ascii=False),
            ))
            zone_rows += [(now, cam["id"], z["id"], z["kind"], z["name"], z["inside"]) for z in cr.get("zones") or []]
        with conn:
            if rows:
                conn.executemany(
                    "INSERT INTO samples (ts, camera_id, persons, entries, exits, alerts, objects) VALUES (?,?,?,?,?,?,?)",
                    rows,
                )
            if zone_rows:
                conn.executemany(
                    "INSERT INTO zone_samples (ts, camera_id, zone_id, kind, name, inside) VALUES (?,?,?,?,?,?)",
                    zone_rows,
                )

    def _flush_heat(self, conn: sqlite3.Connection, now: float) -> None:
        """Add each worker's new heat grid to that camera's row for the current hour."""
        if self._heat is None or not self.enabled:
            return
        hour = float(int(now // 3600) * 3600)
        for cam_id, grid in (self._heat() or {}).items():
            if grid is None:
                continue
            rows, cols = grid.shape
            old = conn.execute(
                "SELECT cols, rows, grid FROM heat WHERE hour_ts = ? AND camera_id = ?", (hour, cam_id)
            ).fetchone()
            values = [round(float(v), 1) for v in grid.ravel()]
            if old is not None and old["cols"] == cols and old["rows"] == rows:
                values = [round(a + b, 1) for a, b in zip(json.loads(old["grid"]), values)]
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO heat (hour_ts, camera_id, cols, rows, grid) VALUES (?,?,?,?,?)",
                    (hour, cam_id, cols, rows, json.dumps(values)),
                )

    def _purge(self, conn: sqlite3.Connection, now: float) -> None:
        days = float(self._cfg().get("retention_days", 30))
        if days <= 0:
            return
        cutoff = now - days * 86400.0
        with conn:
            conn.execute("DELETE FROM events WHERE ts < ? AND type != 'reset'", (cutoff,))
            conn.execute("DELETE FROM tracks WHERE last_ts < ?", (cutoff,))
            conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            conn.execute("DELETE FROM zone_samples WHERE ts < ?", (cutoff,))
            conn.execute("DELETE FROM heat WHERE hour_ts < ?", (cutoff,))

    # ---------- queries ----------
    def counts_since_reset(self, camera_id: str) -> dict:
        """{zone_id | "_line" | None: (entries, exits)} of crossing events since the last reset.

        Line events have mode "line"; zone events carry data.zone_id. Zone events saved before zones had ids
        come back under None (CrossingCounter gives them to the primary zone).
        """
        with self._read() as conn:
            row = conn.execute(
                "SELECT MAX(ts) FROM events WHERE camera_id = ? AND type = 'reset'", (camera_id,)
            ).fetchone()
            since = row[0] or 0.0
            rows = conn.execute(
                "SELECT CASE WHEN json_extract(data, '$.mode') = 'line' THEN '_line' "
                "ELSE json_extract(data, '$.zone_id') END AS zone, direction, COUNT(*) "
                "FROM events WHERE camera_id = ? AND type = 'crossing' AND ts > ? GROUP BY zone, direction",
                (camera_id, since),
            ).fetchall()
        out: dict = {}
        for zone, direction, n in rows:
            n_in, n_out = out.get(zone, (0, 0))
            out[zone] = (n_in + n, n_out) if direction == "in" else (n_in, n_out + n)
        return out

    def summary(self) -> dict:
        with self._read() as conn:
            by_type = {r[0]: r[1] for r in conn.execute("SELECT type, COUNT(*) FROM events GROUP BY type")}
            tracks = conn.execute("SELECT COUNT(*), SUM(alert) FROM tracks").fetchone()
            samples = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            span = conn.execute(
                "SELECT MIN(t), MAX(t) FROM (SELECT MIN(ts) t FROM events UNION ALL SELECT MAX(ts) FROM events "
                "UNION ALL SELECT MIN(first_ts) FROM tracks UNION ALL SELECT MAX(last_ts) FROM tracks)"
            ).fetchone()
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {
            "path": str(self.path),
            "size_bytes": size,
            "events": by_type,
            "tracks": tracks[0] or 0,
            "alert_tracks": tracks[1] or 0,
            "samples": samples,
            "first_ts": span[0],
            "last_ts": span[1],
            "queue": self._q.qsize(),
            "dropped": self.dropped,
            "written": self.written,
            "error": self.last_error,
        }

    @staticmethod
    def _where(clauses: list[str], params: list, *, ts_col: str, camera_id, ts_from, ts_to) -> None:
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if ts_from is not None:
            clauses.append(f"{ts_col} >= ?")
            params.append(float(ts_from))
        if ts_to is not None:
            clauses.append(f"{ts_col} < ?")
            params.append(float(ts_to))

    def list_events(
        self, *, camera_id=None, types: list[str] | None = None, ts_from=None, ts_to=None, limit=100, offset=0
    ) -> dict:
        clauses, params = [], []
        self._where(clauses, params, ts_col="ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
        if types:
            clauses.append(f"type IN ({','.join('?' * len(types))})")
            params.extend(types)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM events {where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM events {where} ORDER BY ts DESC LIMIT ? OFFSET ?", params + [int(limit), int(offset)]
            ).fetchall()
        return {"total": total, "rows": [self._event_row(r) for r in rows]}

    def list_tracks(
        self, *, camera_id=None, ts_from=None, ts_to=None, alert_only=False, item=None, limit=100, offset=0
    ) -> dict:
        clauses, params = [], []
        self._where(clauses, params, ts_col="first_ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
        if alert_only:
            clauses.append("alert = 1")
        if item:
            clauses.append("items LIKE ?")
            params.append(f'%"{item}"%')
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM tracks {where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM tracks {where} ORDER BY first_ts DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
        return {"total": total, "rows": [self._track_row(r) for r in rows]}

    def hourly(self, *, camera_id=None, ts_from=None, ts_to=None) -> list[dict]:
        off = _local_offset_s()
        clauses, params = [], []
        self._where(clauses, params, ts_col="ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        buckets: dict[int, dict] = {}
        with self._read() as conn:
            for b, typ, direction, n in conn.execute(
                f"SELECT CAST((ts + {off}) / 3600 AS INTEGER) b, type, direction, COUNT(*) FROM events {where} "
                "GROUP BY b, type, direction",
                params,
            ):
                d = buckets.setdefault(b, {"in": 0, "out": 0, "alerts": 0, "tracks": 0})
                if typ == "crossing" and direction in ("in", "out"):
                    d[direction] += n
                elif typ in ("alert", "ppe_alert"):
                    d["alerts"] += n
            clauses2, params2 = [], []
            self._where(clauses2, params2, ts_col="first_ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
            where2 = f"WHERE {' AND '.join(clauses2)}" if clauses2 else ""
            for b, n in conn.execute(
                f"SELECT CAST((first_ts + {off}) / 3600 AS INTEGER) b, COUNT(*) FROM tracks {where2} GROUP BY b",
                params2,
            ):
                buckets.setdefault(b, {"in": 0, "out": 0, "alerts": 0, "tracks": 0})["tracks"] += n
        return [{"hour_ts": b * 3600 - off, **v} for b, v in sorted(buckets.items())]

    def _zone_tracks(self, camera_id, ts_from, ts_to) -> list[tuple[str, float, dict, bool | None]]:
        """(camera_id, first_ts, zones dict, visitor) of the tracks in the range."""
        clauses, params = [], []
        self._where(clauses, params, ts_col="first_ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        out = []
        with self._read() as conn:
            for r in conn.execute(f"SELECT camera_id, first_ts, zones, visitor FROM tracks {where} LIMIT 200000", params):
                try:
                    zones = json.loads(r["zones"] or "{}")
                except json.JSONDecodeError:
                    zones = {}
                out.append((r["camera_id"], r["first_ts"], zones, None if r["visitor"] is None else bool(r["visitor"])))
        return out

    def zone_summary(self, *, camera_id=None, ts_from=None, ts_to=None, visitor_min_s: float = 10) -> list[dict]:
        """Per zone: visits, visitors (>= visitor_min_s inside), dwell avg / median of visitors, entries, peak, crowds."""
        acc: dict[tuple, dict] = {}
        for cam, _, zones, _visitor in self._zone_tracks(camera_id, ts_from, ts_to):
            for zid, z in zones.items():
                a = acc.setdefault((cam, zid), {"camera_id": cam, "zone_id": zid, "kind": z.get("kind"),
                                                "name": z.get("name"), "visits": 0, "dwell": []})
                a["kind"], a["name"] = z.get("kind") or a["kind"], z.get("name") or a["name"]
                a["visits"] += 1
                if float(z.get("seconds") or 0) >= visitor_min_s:
                    a["dwell"].append(float(z["seconds"]))
        clauses, params = [], []
        self._where(clauses, params, ts_col="ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
        where = " AND ".join(clauses + ["1"])
        with self._read() as conn:
            for cam, zid, kind, name, peak, avg in conn.execute(
                f"SELECT camera_id, zone_id, kind, name, MAX(inside), AVG(inside) FROM zone_samples WHERE {where} "
                "GROUP BY camera_id, zone_id", params,
            ):
                a = acc.setdefault((cam, zid), {"camera_id": cam, "zone_id": zid, "kind": kind, "name": name,
                                                "visits": 0, "dwell": []})
                a["peak"], a["avg_inside"] = peak, round(avg or 0, 2)
            for cam, zid, typ, direction, n in conn.execute(
                "SELECT camera_id, json_extract(data, '$.zone_id') z, type, direction, COUNT(*) FROM events "
                f"WHERE {where} AND type IN ('crossing', 'crowd') AND json_extract(data, '$.zone_id') IS NOT NULL "
                "AND (type = 'crossing' OR json_extract(data, '$.state') = 'start') GROUP BY camera_id, z, type, direction",
                params,
            ):
                a = acc.get((cam, zid))
                if a is None:
                    continue
                key = "crowds" if typ == "crowd" else ("entries" if direction == "in" else "exits")
                a[key] = a.get(key, 0) + n
        out = []
        for a in acc.values():
            d = sorted(a.pop("dwell"))
            out.append({
                **a, "visitors": len(d),
                "dwell_avg_s": round(sum(d) / len(d), 1) if d else None,
                "dwell_median_s": round(d[len(d) // 2] if len(d) % 2 else (d[len(d) // 2 - 1] + d[len(d) // 2]) / 2, 1)
                if d else None,
                "entries": a.get("entries", 0), "exits": a.get("exits", 0), "crowds": a.get("crowds", 0),
                "peak": a.get("peak"), "avg_inside": a.get("avg_inside"),
            })
        order = ["booth", "game_3d", "monitoring", "reception", "other"]
        out.sort(key=lambda r: (r["camera_id"], order.index(r["kind"]) if r["kind"] in order else 99, r["name"] or ""))
        return out

    def visitors_hourly(self, *, camera_id=None, zone_id=None, ts_from=None, ts_to=None,
                        visitor_min_s: float = 10) -> list[dict]:
        """Visitors per local hour: tracks flagged visitor (booth), or >= visitor_min_s in zone_id when given."""
        off = _local_offset_s()
        buckets: dict[int, dict] = {}
        for _, first_ts, zones, visitor in self._zone_tracks(camera_id, ts_from, ts_to):
            b = int((first_ts + off) // 3600)
            d = buckets.setdefault(b, {"visitors": 0, "visits": 0})
            if zone_id:
                z = zones.get(zone_id)
                if not z:
                    continue
                d["visits"] += 1
                d["visitors"] += float(z.get("seconds") or 0) >= visitor_min_s
            else:
                d["visits"] += bool(zones)
                d["visitors"] += bool(visitor)
        return [{"hour_ts": b * 3600 - off, **v} for b, v in sorted(buckets.items())]

    def heatmap(self, *, camera_id: str, ts_from=None, ts_to=None) -> dict | None:
        """Summed heat grid (seconds per cell) of the hours in range; None when nothing was recorded."""
        clauses, params = ["camera_id = ?"], [camera_id]
        if ts_from is not None:
            clauses.append("hour_ts >= ?")
            params.append(float(ts_from) // 3600 * 3600)
        if ts_to is not None:
            clauses.append("hour_ts < ?")
            params.append(float(ts_to))
        total = None
        cols = rows = 0
        with self._read() as conn:
            for r in conn.execute(f"SELECT cols, rows, grid FROM heat WHERE {' AND '.join(clauses)}", params):
                g = json.loads(r["grid"])
                if total is None or (r["cols"], r["rows"]) != (cols, rows):
                    if total is not None:
                        continue  # grid size changed mid-range: keep the first size
                    cols, rows, total = r["cols"], r["rows"], [0.0] * len(g)
                total = [a + b for a, b in zip(total, g)]
        return None if total is None else {"cols": cols, "rows": rows, "grid": total}

    def top_items(self, *, camera_id=None, ts_from=None, ts_to=None, limit=30) -> list[dict]:
        clauses, params = [], []
        self._where(clauses, params, ts_col="first_ts", camera_id=camera_id, ts_from=ts_from, ts_to=ts_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        counter: Counter = Counter()
        with self._read() as conn:
            for (items,) in conn.execute(f"SELECT items FROM tracks {where} ORDER BY first_ts DESC LIMIT 50000", params):
                try:
                    counter.update(json.loads(items or "{}").keys())
                except json.JSONDecodeError:
                    continue
        return [{"label": k, "tracks": v} for k, v in counter.most_common(limit)]

    def export_csv(self, kind: str, **filters) -> Iterator[str]:
        page, offset = 2000, 0
        buf = io.StringIO()
        writer = csv.writer(buf)
        lister = self.list_events if kind == "events" else self.list_tracks
        header_written = False
        while True:
            chunk = lister(limit=page, offset=offset, **filters)["rows"]
            if not chunk:
                break
            if not header_written:
                writer.writerow(list(chunk[0].keys()) + ["time_local"])
                header_written = True
            for r in chunk:
                ts = r.get("ts") or r.get("first_ts")
                writer.writerow(
                    [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in r.values()]
                    + [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""]
                )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
            offset += page

    def clear(self) -> None:
        with self._read() as conn:
            with conn:
                conn.execute("DELETE FROM events")
                conn.execute("DELETE FROM tracks")
                conn.execute("DELETE FROM samples")
                conn.execute("DELETE FROM zone_samples")
                conn.execute("DELETE FROM heat")
            conn.execute("VACUUM")

    @staticmethod
    def _event_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["data"] = json.loads(d["data"]) if d.get("data") else None
        return d

    @staticmethod
    def _track_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["items"] = json.loads(d["items"]) if d.get("items") else {}
        d["alert"] = bool(d.get("alert"))
        d["zones"] = json.loads(d["zones"]) if d.get("zones") else {}
        d["visitor"] = None if d.get("visitor") is None else bool(d["visitor"])
        return d
