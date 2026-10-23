"""Entry point: python -m app [--host 127.0.0.1] [--port 8000] [--device 0|cpu] [--no-browser]."""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exhibition multi-camera tracking server")
    parser.add_argument("--host", default=os.environ.get("EXHIBITION_HOST", "127.0.0.1"),
                        help="0.0.0.0 to allow other devices on the LAN")
    parser.add_argument("--port", type=int, default=int(os.environ.get("EXHIBITION_PORT", "8000")))
    parser.add_argument("--device", default=None, help="CUDA index like 0, or cpu (overrides settings.json)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    from .paths import MODELS_DIR

    # Ultralytics resolves auto-downloads against the cwd.
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(MODELS_DIR)

    import uvicorn

    from .main import create_app

    app = create_app(device=args.device)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    print(f"[exhibition] open {url}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
