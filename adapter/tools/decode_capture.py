"""Decode a sniffer capture (frames.bin) into readable JSON lines.

Usage: uv run python tools/decode_capture.py <ws-dir> [--full]
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from quasar_adapter import aapi

SECRET_KEYS = {"AuthToken", "OAuthToken", "auth_token", "oauth_token", "Token", "ApplicationToken"}


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in SECRET_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _shorten(obj, limit: int):
    if isinstance(obj, dict):
        return {k: _shorten(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shorten(v, limit) for v in obj]
    if isinstance(obj, str) and len(obj) > limit:
        return obj[:limit] + f"...(+{len(obj) - limit})"
    return obj


def iter_frames(path: Path):
    data = path.read_bytes()
    pos = 0
    while pos + 6 <= len(data):
        direction, kind, length = struct.unpack(">BBI", data[pos:pos + 6])
        pos += 6
        yield direction, kind, data[pos:pos + length]
        pos += length


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ws_dir", type=Path)
    parser.add_argument("--full", action="store_true", help="do not shorten long strings")
    args = parser.parse_args()

    audio: dict[int, list[int]] = {}
    for n, (direction, kind, payload) in enumerate(iter_frames(args.ws_dir / "frames.bin"), 1):
        arrow = "->" if direction == 0 else "<-"
        if kind == 0:
            print(f"#{n} {arrow} TEXT {payload[:500]!r}")
            continue
        if aapi.is_aapi(payload):
            msg = aapi.parse_client(payload) if direction == 0 else aapi.parse_server(payload)
            body = _redact(aapi.to_dict(msg))
            if not args.full:
                body = _shorten(body, 200)
            print(f"#{n} {arrow} AAPI {json.dumps(body, ensure_ascii=False)}")
            continue
        stream_id, chunk = aapi.split_stream_frame(payload)
        stats = audio.setdefault(stream_id, [0, 0])
        if stats[0] == 0 or chunk[:4] == b"OggS" and chunk[5] & 0x02:
            print(f"#{n} {arrow} STREAM {stream_id} len={len(chunk)} head={chunk[:32].hex()}")
        stats[0] += 1
        stats[1] += len(chunk)
    for stream_id, (count, size) in audio.items():
        print(f"stream {stream_id}: {count} frames, {size} bytes")


if __name__ == "__main__":
    main()
