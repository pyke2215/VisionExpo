"""FastAPI server: pages, REST API, MJPEG streams, WebSocket push, database queries."""

from __future__ import annotations

import asyncio
import base64
import time
from contextlib import asynccontextmanager

import cv2
from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .capture import grab_single_frame
from .crossing import ALLOWED_TASKS, MAX_ZONE_POINTS
from .settings_store import ZONE_TASKS
from .manager import CameraManager
from .paths import SHOW_DIRS, WEB_DIR
from .settings_store import SettingsStore
from .ws import TOPICS, WsHub

BOUNDARY = "frame"


def _with_bom(chunks):
    """UTF-8 BOM first so Excel opens Vietnamese text correctly."""
    yield "\ufeff"
    yield from chunks


def create_app(device: str | None = None) -> FastAPI:
    store = SettingsStore()
    manager = CameraManager(store, device_override=device)
    ws_hub = WsHub(manager, store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        manager.start()
        await ws_hub.start()
        yield
        await ws_hub.stop()
        manager.shutdown()

    app = FastAPI(title="Exhibition Camera Tracking", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    # Local 3D show dashboard (Greco2026DashBoard build, git-ignored): http://HOST:PORT/show/
    show_dir = next((d for d in SHOW_DIRS if (d / "index.html").is_file()), None)
    if show_dir is not None:
        app.mount("/show", StaticFiles(directory=show_dir, html=True), name="show")

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        # Without it browsers heuristically cache js/css and keep running old code after an update
        # (new HTML, old live.js). no-cache = revalidate each load; unchanged files answer 304.
        response = await call_next(request)
        if request.url.path.startswith(("/static/", "/show")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def _worker_or_404(cam_id: str):
        w = manager.worker(cam_id)
        if w is None:
            raise HTTPException(404, f"camera '{cam_id}' is not running")
        return w

    def _camera_or_404(cam_id: str) -> dict:
        cam = store.camera(cam_id)
        if cam is None:
            raise HTTPException(404, f"camera '{cam_id}' not found")
        return cam

    # ---------- pages ----------
    def _page(name: str) -> FileResponse:
        return FileResponse(WEB_DIR / name, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def page_live():
        return _page("index.html")

    @app.get("/cameras", include_in_schema=False)
    def page_cameras():
        return _page("cameras.html")

    @app.get("/calibrate", include_in_schema=False)
    @app.get("/calibrate/{cam_id}", include_in_schema=False)
    def page_calibrate(cam_id: str | None = None):
        return _page("calibrate.html")

    @app.get("/ppe", include_in_schema=False)
    def page_ppe():
        return _page("ppe.html")

    @app.get("/socket", include_in_schema=False)
    def page_socket():
        return _page("socket.html")

    @app.get("/data", include_in_schema=False)
    def page_data():
        return _page("data.html")

    # ---------- status ----------
    @app.get("/api/status")
    def api_status():
        return manager.status()

    # ---------- cameras ----------
    @app.get("/api/cameras")
    def api_cameras():
        return store.cameras()

    @app.post("/api/cameras")
    def api_add_camera(payload: dict = Body(...)):
        if not str(payload.get("source") or "").strip():
            raise HTTPException(400, "source is required (RTSP URL, webcam index or video path)")
        try:
            return manager.add_camera(payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/cameras/test")
    def api_test_source(payload: dict = Body(...)):
        source = str(payload.get("source") or "").strip()
        if not source:
            raise HTTPException(400, "source is required")
        try:
            frame = grab_single_frame(source)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        h, w = frame.shape[:2]
        if w > 960:
            frame = cv2.resize(frame, (960, int(h * 960 / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return {
            "ok": True,
            "width": w,
            "height": h,
            "image": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else None,
        }

    @app.get("/api/cameras/{cam_id}")
    def api_camera(cam_id: str):
        return _camera_or_404(cam_id)

    @app.put("/api/cameras/{cam_id}")
    def api_update_camera(cam_id: str, payload: dict = Body(...)):
        _camera_or_404(cam_id)
        return manager.update_camera(cam_id, payload)

    @app.delete("/api/cameras/{cam_id}")
    def api_delete_camera(cam_id: str):
        _camera_or_404(cam_id)
        manager.remove_camera(cam_id)
        return {"ok": True}

    @app.post("/api/cameras/{cam_id}/restart")
    def api_restart_camera(cam_id: str):
        _camera_or_404(cam_id)
        manager.restart_camera(cam_id)
        return {"ok": True}

    # ---------- crossing ----------
    @app.put("/api/cameras/{cam_id}/crossing")
    def api_set_crossing(cam_id: str, payload: dict = Body(...)):
        cam = _camera_or_404(cam_id)
        mode = payload.get("mode", "line")
        if mode not in ("line", "zone"):
            raise HTTPException(400, "mode must be 'line' or 'zone'")
        pts = payload.get("points") or []
        zone = payload.get("zone") or []
        if pts and len(pts) != 2:
            raise HTTPException(400, "points must contain exactly 2 points (or be empty to clear)")
        if zone and not 3 <= len(zone) <= MAX_ZONE_POINTS:
            raise HTTPException(400, f"zone must contain 3..{MAX_ZONE_POINTS} points (or be empty to clear)")
        inward = payload.get("inward_point")
        for p in pts + zone + ([inward] if inward else []):
            if not (0.0 <= float(p.get("x", -1)) <= 1.0 and 0.0 <= float(p.get("y", -1)) <= 1.0):
                raise HTTPException(400, "points must be normalized to 0..1")
        norm = lambda p: {"x": float(p["x"]), "y": float(p["y"])}  # noqa: E731
        if "tasks" in payload:
            raw_tasks = payload.get("tasks") or []
            if not isinstance(raw_tasks, list):
                raise HTTPException(400, "tasks must be a list")
            tasks = []
            for t in raw_tasks:
                key = str(t).lower().strip()
                if key not in ALLOWED_TASKS:
                    raise HTTPException(400, f"unknown task '{t}'; allowed: {sorted(ZONE_TASKS)}")
                if key not in tasks:
                    tasks.append(key)
            if mode == "line":
                tasks = [t for t in tasks if t == "count"]
        else:
            # Backward compat: enabled checkbox alone → count task
            tasks = ["count"] if bool(payload.get("enabled", True)) else []
        cur = cam.get("crossing") or {}  # keep the rule when the caller omits it
        rule = payload.get("zone_rule", cur.get("zone_rule", "body"))
        if rule not in ("body", "box", "foot"):
            raise HTTPException(400, "zone_rule must be 'body', 'box' or 'foot'")
        overlap = float(min(max(float(payload.get("zone_overlap", cur.get("zone_overlap", 0.5))), 0.1), 0.9))
        crossing = {
            "enabled": "count" in tasks,
            "mode": mode,
            "points": [norm(p) for p in pts],
            "inward_point": norm(inward) if inward else None,
            "zone": [norm(p) for p in zone],
            "tasks": tasks,
            "zone_rule": rule,
            "zone_overlap": overlap,
        }
        return manager.update_camera(cam_id, {"crossing": crossing})["crossing"]

    @app.post("/api/cameras/{cam_id}/crossing/reset")
    def api_reset_crossing(cam_id: str):
        _worker_or_404(cam_id)
        manager.reset_counts(cam_id)
        return {"ok": True}

    @app.post("/api/crossing/reset_all")
    def api_reset_all():
        manager.reset_counts()
        return {"ok": True}

    # ---------- WebSocket ----------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws_hub.handle(ws)

    @app.get("/api/ws")
    def api_ws_info():
        return ws_hub.info()

    @app.put("/api/ws")
    def api_ws_update(payload: dict = Body(...)):
        patch: dict = {}
        if "enabled" in payload:
            patch["enabled"] = bool(payload["enabled"])
        if "status_hz" in payload:
            patch["status_hz"] = float(min(max(float(payload["status_hz"]), 0.0), 30.0))
        if "people_hz" in payload:
            patch["people_hz"] = float(min(max(float(payload["people_hz"]), 0.0), 30.0))
        if "default_topics" in payload:
            patch["default_topics"] = [t for t in payload["default_topics"] or [] if t in TOPICS]
        if "token" in payload:
            patch["token"] = str(payload["token"] or "").strip()
        manager.update_section("websocket", patch)
        return ws_hub.info()

    # ---------- database ----------
    def _range(date_from: float | None, date_to: float | None) -> dict:
        return {"ts_from": date_from, "ts_to": date_to}

    @app.get("/api/db")
    def api_db_summary():
        return {"config": store.get("database"), **manager.db.summary()}

    @app.put("/api/db")
    def api_db_update(payload: dict = Body(...)):
        patch: dict = {}
        for key in ("enabled", "save_tracks", "restore_counts"):
            if key in payload:
                patch[key] = bool(payload[key])
        if "sample_every_s" in payload:
            patch["sample_every_s"] = int(min(max(int(payload["sample_every_s"]), 2), 3600))
        if "retention_days" in payload:
            patch["retention_days"] = int(min(max(int(payload["retention_days"]), 0), 3650))
        return manager.update_section("database", patch)

    @app.get("/api/db/events")
    def api_db_events(
        camera_id: str | None = None,
        types: str | None = None,
        date_from: float | None = Query(None, alias="from"),
        date_to: float | None = Query(None, alias="to"),
        limit: int = Query(100, le=1000),
        offset: int = 0,
    ):
        type_list = [t for t in (types or "").split(",") if t]
        return manager.db.list_events(
            camera_id=camera_id or None, types=type_list or None, limit=limit, offset=offset,
            **_range(date_from, date_to),
        )

    @app.get("/api/db/tracks")
    def api_db_tracks(
        camera_id: str | None = None,
        alert_only: bool = False,
        item: str | None = None,
        date_from: float | None = Query(None, alias="from"),
        date_to: float | None = Query(None, alias="to"),
        limit: int = Query(100, le=1000),
        offset: int = 0,
    ):
        return manager.db.list_tracks(
            camera_id=camera_id or None, alert_only=alert_only, item=item or None, limit=limit, offset=offset,
            **_range(date_from, date_to),
        )

    @app.get("/api/db/hourly")
    def api_db_hourly(
        camera_id: str | None = None,
        date_from: float | None = Query(None, alias="from"),
        date_to: float | None = Query(None, alias="to"),
    ):
        return manager.db.hourly(camera_id=camera_id or None, **_range(date_from, date_to))

    @app.get("/api/db/top_items")
    def api_db_top_items(
        camera_id: str | None = None,
        date_from: float | None = Query(None, alias="from"),
        date_to: float | None = Query(None, alias="to"),
    ):
        return manager.db.top_items(camera_id=camera_id or None, **_range(date_from, date_to))

    @app.get("/api/db/export/{kind}.csv")
    def api_db_export(
        kind: str,
        camera_id: str | None = None,
        date_from: float | None = Query(None, alias="from"),
        date_to: float | None = Query(None, alias="to"),
    ):
        if kind not in ("events", "tracks"):
            raise HTTPException(404, "kind must be events or tracks")
        name = f"{kind}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        gen = manager.db.export_csv(kind, camera_id=camera_id or None, **_range(date_from, date_to))
        return StreamingResponse(
            _with_bom(gen),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/db/clear")
    def api_db_clear(payload: dict = Body(...)):
        if payload.get("confirm") != "DELETE":
            raise HTTPException(400, 'send {"confirm": "DELETE"} to wipe the database')
        manager.db.clear()
        return {"ok": True}

    # ---------- images ----------
    @app.get("/api/cameras/{cam_id}/snapshot.jpg")
    def api_snapshot(cam_id: str):
        jpg = _worker_or_404(cam_id).raw_jpeg()
        if jpg is None:
            raise HTTPException(503, "no frame yet")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/frame.jpg")
    def api_frame(cam_id: str):
        jpg, _ = _worker_or_404(cam_id).latest_jpeg()
        if jpg is None:
            raise HTTPException(503, "no frame yet")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/stream.mjpg")
    async def api_stream(cam_id: str, request: Request):
        _worker_or_404(cam_id)

        async def gen():
            last = -1
            idle = 0
            while True:
                w = manager.worker(cam_id)
                if w is None or await request.is_disconnected():
                    break
                jpg, seq = w.latest_jpeg()
                if jpg is not None and seq != last:
                    last = seq
                    idle = 0
                    yield (
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpg)}\r\n\r\n".encode()
                        + jpg
                        + b"\r\n"
                    )
                else:
                    idle += 1
                    await asyncio.sleep(0.02 if idle < 250 else 0.2)

        return StreamingResponse(
            gen(),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    # ---------- PPE detector ----------
    @app.get("/api/ppe")
    def api_ppe():
        return manager.ppe.info()

    @app.put("/api/ppe")
    def api_update_ppe(payload: dict = Body(...)):
        allowed = {"enabled", "mode", "conf", "imgsz", "every_n", "infer_missing", "require", "confirm_runs",
                   "person_mask", "mask_imgsz"}
        patch = {k: v for k, v in payload.items() if k in allowed}
        if "mode" in patch and patch["mode"] not in ("crop", "frame"):
            raise HTTPException(400, "mode must be 'crop' or 'frame'")
        if "mask_imgsz" in patch:
            patch["mask_imgsz"] = int(min(max(int(patch["mask_imgsz"]), 160), 1280)) // 32 * 32
        for key in ("enabled", "infer_missing", "person_mask"):
            if key in patch:
                patch[key] = bool(patch[key])
        if "require" in patch:
            if not isinstance(patch["require"], list) or any(k not in ("hardhat", "gloves") for k in patch["require"]):
                raise HTTPException(400, "require must be a list of 'hardhat' / 'gloves'")
        if "confirm_runs" in patch:
            patch["confirm_runs"] = int(min(max(int(patch["confirm_runs"]), 1), 5))
        if "conf" in patch:
            patch["conf"] = float(min(max(float(patch["conf"]), 0.01), 0.95))
        if "imgsz" in patch:
            patch["imgsz"] = int(min(max(int(patch["imgsz"]), 160), 1280)) // 32 * 32
        if "every_n" in patch:
            patch["every_n"] = int(min(max(int(patch["every_n"]), 1), 30))
        manager.update_ppe(patch)
        return manager.ppe.info()

    return app
