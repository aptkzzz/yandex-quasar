"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="quasar-adapter")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the adapter")
    serve.add_argument("-c", "--config", type=Path, default=Path("config.toml"))

    sniff = sub.add_parser("sniff", help="record station traffic (phase 0)")
    sniff.add_argument("--host", default="0.0.0.0")
    sniff.add_argument("--port", type=int, default=8080)
    sniff.add_argument("--out", type=Path, default=Path("../captures/traffic"))
    sniff.add_argument("--zstd-dict", type=Path, default=Path("../captures/device/zstd.dict"))
    sniff.add_argument(
        "--reply-audio", type=Path, default=None,
        help="spike: answer every voice request with this Ogg/Opus file",
    )

    args = parser.parse_args()
    level = "DEBUG" if args.verbose else os.environ.get("QUASAR_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "serve":
        from .config import Config
        from .server import run

        run(Config.load(args.config))
    elif args.command == "sniff":
        from .sniffer import run_sniffer

        run_sniffer(args.host, args.port, args.out, args.zstd_dict, args.reply_audio)


if __name__ == "__main__":
    main()
