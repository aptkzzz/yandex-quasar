"""Serve adapter/assets over HTTP with Range support (python -m http.server lacks it; the station seeks via Range).

Usage: AIOHTTP_NOSENDFILE=1 uv run python tools/media_server.py [--port 8099]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from aiohttp import web

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    app = web.Application()
    app.router.add_static("/", ASSETS, show_index=True)
    web.run_app(app, port=args.port)


if __name__ == "__main__":
    main()
