# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Exhibition multi-camera tracking server ("Vision Expo"): one Python process (FastAPI + Uvicorn) reads RTSP / webcam / video sources, tracks people (YOLO pose + ByteTrack), counts line / polygon entries, checks PPE (hardhat / gloves) inside zones, stores to SQLite and pushes over WebSocket. Frontend is plain HTML + JS in `web/` (no build step). Docs and UI text are in Vietnamese.

The open-vocabulary object detector (YOLOE + CLIP: `world.py`, `clip_verify.py`, `items.py`, `objtrack.py`, `vocabulary.py`, `/world` page) is **temporarily removed**; `751e2bd` is the last commit that has it. Don't reintroduce `objects` / `items` / weapon `alert` fields unless asked to restore it.

`Greco2026DashBoard/` is a separate, git-ignored Vite + TypeScript + three.js project (its own git repo): the 3D exhibition screen. It only consumes `/ws` and `stream.mjpg`; `build_show.bat` builds it into `show/` (tracked in this repo, with `show/VERSION.txt` naming the Greco commit) so exhibition PCs get the latest screen with `git pull`, no Node; after changing Greco, rebuild and commit `show/`. The server mounts it at `/show/`. Payload changes must be mirrored in `src/live/types.ts` / `store.ts` there (`npx tsc --noEmit` to check).

**Read first:** `docs/GRAPH.md` is a graph-form map of the code (node index, thread model, per-frame pipeline, PPE subgraph, config key → consumer, event → topic/DB, endpoint ↔ page, invariants). `docs/CODE.md` has per-module algorithm detail and change recipes; `docs/WEBSOCKET_API.md` is the external contract.

## Commands

Python lives in the parent folder's venv (`start.bat` looks for `python\`, `.venv\`, then `..\.venv\`). From `Exhibition/`:

```powershell
..\.venv\Scripts\python.exe -m app                                  # server on 127.0.0.1:8000, opens browser
..\.venv\Scripts\python.exe -m app --port 8011 --no-browser          # dev instance beside a running one
..\.venv\Scripts\python.exe -m app --host 0.0.0.0 --device cpu       # LAN access / no GPU
..\.venv\Scripts\python.exe -m py_compile app\*.py                    # syntax check (no test suite, no linter)
node --check web\js\live.js                                          # syntax check a page script
..\.venv\Scripts\python.exe tools\prepare_models.py                  # download pose + seg weights (needs internet)
build_show.bat nopause                                               # build Greco2026DashBoard into show/ (needs Node)
```

`setup.bat` creates the env; `build_portable.bat` → `dist\VisionExpo`; `docker compose up -d --build`.

## Architecture (big picture)

- `__main__` chdirs into `models/` (Ultralytics resolves downloads against cwd) — always build paths via `app/paths.py`.
- `CameraManager` owns one `CameraWorker` thread per enabled camera plus the **shared** GPU service `PpeService` (with an `_infer_lock`), an `EventHub`, and `Database`.
- `CameraWorker._process(frame)` is the per-frame orchestrator and reads top-down as the pipeline: `PersonTracker.track` → `CrossingCounter.update` (sets `Person.side`) → PPE → `_update_tracks` / events → `draw.render` → JPEG for MJPEG. `Person` (`app/person.py`) is the object every stage annotates.
- Workers only read the **latest** frame from `FrameReader` (no queue). Shared outputs (`worker.persons`, `ppe_findings`) are replaced with new lists each frame, never mutated in place. `EventHub` subscribers run on the worker thread and must not block (DB uses a `queue.Queue`, `WsHub` uses `call_soon_threadsafe`).
- Config: `settings_store.GLOBAL_DEFAULTS` / `CAMERA_DEFAULTS` are the single source of defaults; `config/settings.json` is deep-merged over them on load. The server **overwrites** `settings.json` on every save, so hand edits while it runs are lost, and two servers share the same file. Change live config through the REST API (`PUT /api/ppe`, `/api/cameras/{id}`, …; PPE settings UI is the `/ppe` page); `RESTART_FIELDS` (`source`, `enabled`) recreate the worker, everything else is hot-applied. New config keys must also be added to the endpoint's `allowed` set in `main.py` or they are silently dropped.
- Zone geometry is normalized 0..1 and judged on the **foot point**; zones are drawn on the floor, so anything on the upper body (hardhat) sits outside the polygon — attach by owner person, not by the item's own position.

## PPE: what is non-obvious

- `yolo_ppe.pt` is effectively blind on the venue camera (bare head `NO-Hardhat` < 0.03, dark gloves `Gloves` < 0.04 even on upscaled crops). PPE state is therefore **inferred** in `app/ppe.py` from pose keypoints + pixels (`cover_state`, `hand_state`, `crown_cover`), accumulated per track in `PpeEvidence` (5-run window, `confirm_runs`), gloves per hand (`gloves_L` / `gloves_R`). Inferred findings carry `inferred=True`. Don't "fix" misses by tuning `ppe.conf` alone.
- Gloves are judged on skin share inside a circle just past the wrist, restricted to the person's own pixels via `yolo26n-seg.pt` masks (`PpeService.person_masks`); a hand off the mask is never judged bare (skin-coloured chairs/desks read as skin). Missing seg model → masks off, PPE still runs.
- Zone membership for PPE has a 2 s grace (`PPE_ZONE_GRACE_S`) and crossing sides need `SIDE_CONFIRM_FRAMES` consecutive frames — both exist because turning/bending makes keypoints jitter across the border.
- Measured thresholds and why they are what they are are documented in `README.md` (config table) and `docs/GRAPH.md` §4; re-measure on real frames before changing them.

## Verifying changes

There are no automated tests. The cameras sit on an isolated LAN, so live frames are usually unavailable — reproduce offline instead:

- Run the real pipeline on an image without starting the server or touching `settings.json`: build `CameraWorker(cam_cfg, person_weights=..., device=..., ppe=PpeService(...))` and call `w._process(img)` repeatedly (do **not** call `w.start()`); inspect `w.persons`, `w.ppe_findings`, `w.people_detail()`, `w.latest_jpeg()`. See `docs/GRAPH.md` §9.
- Dashboard screenshots carry the overlay (drawn skeleton/boxes defeat the pose model, zone fill tints colours): inpaint red/purple/green/white overlay pixels before re-running pose, and treat colour thresholds measured on screenshots as approximate.
- To check the web UI, run an isolated instance on another port with `app.paths.SETTINGS_FILE` / `DATABASE_FILE` patched to temp files **before** importing `app.main`, and a looping video file as the camera source.
- `/static` is served with `Cache-Control: no-cache`; after JS/CSS changes a browser that cached the old version needs one hard reload.

## Repo conventions

- Weights (`models/*.pt`, `*.ts`, `*.json`), `config/settings.json` (contains RTSP passwords), `data/` and `Greco2026DashBoard/` (source; its build `show/` is tracked) are git-ignored. `yolo_ppe.pt` is copied in by hand; others come from `tools/prepare_models.py`.
- OpenCV overlay text must be ASCII (`draw.ascii_text`); Vietnamese only in the web UI / data.
- When adding an event type: emit in the worker, map it in `ws.EVENT_TOPIC` if not `events`, add it to `EVENT_TYPES` in `web/js/data.js`, document in `docs/WEBSOCKET_API.md` and `web/socket.html`. Keep `docs/GRAPH.md` in sync when module relationships change.
