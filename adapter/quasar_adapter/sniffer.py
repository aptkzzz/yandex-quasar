"""Phase 0 traffic recorder.

Accepts the station's uniproxy WebSocket and arbitrary HTTP backend requests and writes everything to disk so the
real protocol can be studied. It does not answer voice requests in a meaningful way.

Layout of a capture session directory:
    session.jsonl          one JSON line per HTTP request / WS frame (metadata + short previews)
    ws-<n>/frames.bin      raw WS frames: [u8 direction][u8 kind][u32 BE length][payload]
    ws-<n>/stream-<id>.bin concatenated payloads of binary audio frames (stream id prefix stripped)
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from pathlib import Path
from typing import Any

import zstandard
from aiohttp import WSMsgType, web

_LOGGER = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).resolve().parent.parent / "assets"
ZSTD_MAGIC =b"\x28\xb5\x2f\xfd"
AAPI_MAGIC = b"AAPI"
SECRET_HEADERS = {
    "authorization", "x-uprx-auth-token", "x-uprx-oauth-token", "cookie", "x-quasar-signature",
}

DIR_IN = 0  # station -> adapter
DIR_OUT = 1  # adapter -> station
KIND_TEXT = 0
KIND_BINARY = 1


class Recorder:
    """Appends capture records to a session directory."""

    def __init__(self, root: Path, zstd_dict: Path | None) -> None:
        self.dir = root / time.strftime("%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._log = (self.dir / "session.jsonl").open("a", encoding="utf-8")
        self._ws_counter = 0
        self._zstd = None
        if zstd_dict and zstd_dict.exists():
            self._zstd = zstandard.ZstdDecompressor(
                dict_data=zstandard.ZstdCompressionDict(zstd_dict.read_bytes())
            )
        _LOGGER.info("Recording to %s", self.dir)

    def write(self, record: dict[str, Any]) -> None:
        record["ts"] = round(time.time(), 3)
        self._log.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._log.flush()

    def next_ws_dir(self) -> Path:
        self._ws_counter += 1
        path = self.dir / f"ws-{self._ws_counter}"
        path.mkdir()
        return path

    def try_unzstd(self, data: bytes) -> bytes | None:
        if self._zstd is None or not data.startswith(ZSTD_MAGIC):
            return None
        try:
            return self._zstd.decompressobj().decompress(data)
        except zstandard.ZstdError as err:
            _LOGGER.debug("zstd decode failed: %s", err)
            return None


def _redact_headers(headers: Any) -> dict[str, str]:
    return {
        k: ("<redacted>" if k.lower() in SECRET_HEADERS else v) for k, v in headers.items()
    }


def _classify(data: bytes) -> str:
    if data.startswith(AAPI_MAGIC):
        return "aapi"
    if data.startswith(ZSTD_MAGIC):
        return "zstd"
    if len(data) >= 4:
        return "stream"
    return "short"


def _preview(data: bytes, size: int = 48) -> str:
    return data[:size].hex()


class Sniffer:
    """aiohttp application recording station traffic."""

    def __init__(self, recorder: Recorder, responder_factory: Any = None) -> None:
        self.recorder = recorder
        self.responder_factory = responder_factory
        self.responders: set[Any] = set()

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("GET", "/uni.ws", self.handle_uniproxy)
        app.router.add_route("GET", "/{host}/uni.ws", self.handle_uniproxy)
        app.router.add_post("/_spike/push", self.handle_spike_push)
        app.router.add_get("/_media/infinite.mp3", self.handle_infinite_mp3)
        app.router.add_get("/_media/redirect/{code}/{name}", self.handle_media_redirect)
        app.router.add_static("/_media/", MEDIA_DIR, show_index=True)
        app.router.add_route("*", "/{tail:.*}", self.handle_http)
        return app

    async def handle_spike_push(self, request: web.Request) -> web.StreamResponse:
        """Debug: POST {"directives": [{type, name, payload}]} to every connected station."""
        body = await request.json()
        for responder in list(self.responders):
            await responder.push(body["directives"])
        return web.json_response({"sent_to": len(self.responders)})

    async def handle_infinite_mp3(self, request: web.Request) -> web.StreamResponse:
        """Endless MP3 without Content-Length, like a Music Assistant flow stream."""
        data = (MEDIA_DIR / "music.mp3").read_bytes()
        resp = web.StreamResponse(headers={"Content-Type": "audio/mpeg"})
        await resp.prepare(request)
        _LOGGER.info("infinite.mp3 started for %s", request.remote)
        try:
            while True:
                for i in range(0, len(data), 16384):
                    await resp.write(data[i:i + 16384])
                    await asyncio.sleep(0.05)
        except (ConnectionResetError, asyncio.CancelledError):
            _LOGGER.info("infinite.mp3 stopped for %s", request.remote)
            raise
        return resp

    async def handle_media_redirect(self, request: web.Request) -> web.StreamResponse:
        return web.Response(
            status=int(request.match_info["code"]),
            headers={"Location": f"/_media/{request.match_info['name']}"},
        )

    async def handle_http(self, request: web.Request) -> web.StreamResponse:
        body = await request.read()
        self.recorder.write(
            {
                "type": "http",
                "method": request.method,
                "host": request.host,
                "path": request.path_qs,
                "headers": _redact_headers(request.headers),
                "body_len": len(body),
                "body_preview": body[:2048].decode("utf-8", "replace"),
            }
        )
        _LOGGER.info("HTTP %s %s%s (%d bytes)", request.method, request.host, request.path_qs, len(body))
        if request.path.endswith("/generate_204"):
            return web.Response(status=204)
        return web.json_response({"status": "ok"})

    async def handle_uniproxy(self, request: web.Request) -> web.StreamResponse:
        ws_dir = self.recorder.next_ws_dir()
        self.recorder.write(
            {
                "type": "ws_open",
                "dir": ws_dir.name,
                "remote": request.remote,
                "path": request.path_qs,
                "headers": _redact_headers(request.headers),
            }
        )
        _LOGGER.info("uniproxy WS from %s -> %s", request.remote, ws_dir.name)

        ws = web.WebSocketResponse(compress=False, max_msg_size=0, autoping=True)
        await ws.prepare(request)
        frames = (ws_dir / "frames.bin").open("ab")
        streams: dict[int, Any] = {}
        counter = 0

        async def send(data: bytes) -> None:
            frames.write(struct.pack(">BBI", DIR_OUT, KIND_BINARY, len(data)) + data)
            frames.flush()
            self.recorder.write(
                {"type": "ws_binary_out", "dir": ws_dir.name, "len": len(data), "kind": _classify(data)}
            )
            await ws.send_bytes(data)

        responder = self.responder_factory(send) if self.responder_factory else None
        if responder is not None:
            self.responders.add(responder)
        try:
            async for msg in ws:
                counter += 1
                if msg.type == WSMsgType.TEXT:
                    data = msg.data.encode()
                    frames.write(struct.pack(">BBI", DIR_IN, KIND_TEXT, len(data)) + data)
                    self.recorder.write(
                        {"type": "ws_text", "dir": ws_dir.name, "n": counter, "len": len(data),
                         "text": msg.data[:4096]}
                    )
                elif msg.type == WSMsgType.BINARY:
                    data = msg.data
                    frames.write(struct.pack(">BBI", DIR_IN, KIND_BINARY, len(data)) + data)
                    self._record_binary(ws_dir, counter, data, streams)
                    if responder is not None and data.startswith(AAPI_MAGIC):
                        try:
                            await responder.handle_aapi(data)
                        except Exception:  # spike: never drop the station connection
                            _LOGGER.exception("responder failed")
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                frames.flush()
        finally:
            self.responders.discard(responder)
            frames.close()
            for fh in streams.values():
                fh.close()
            self.recorder.write(
                {"type": "ws_close", "dir": ws_dir.name, "frames": counter, "close_code": ws.close_code}
            )
            _LOGGER.info("uniproxy WS %s closed after %d frames", ws_dir.name, counter)
        return ws

    def _record_binary(self, ws_dir: Path, n: int, data: bytes, streams: dict[int, Any]) -> None:
        kind = _classify(data)
        record: dict[str, Any] = {
            "type": "ws_binary", "dir": ws_dir.name, "n": n, "len": len(data), "kind": kind,
            "head": _preview(data),
        }
        if kind == "zstd":
            plain = self.recorder.try_unzstd(data)
            if plain is not None:
                record["unzstd_len"] = len(plain)
                record["unzstd_kind"] = _classify(plain)
                record["unzstd_head"] = _preview(plain)
        elif kind == "stream":
            stream_id = struct.unpack(">I", data[:4])[0]
            record["stream_id"] = stream_id
            payload = data[4:]
            record["payload_head"] = _preview(payload, 16)
            if stream_id not in streams:
                streams[stream_id] = (ws_dir / f"stream-{stream_id}.bin").open("ab")
            streams[stream_id].write(payload)
            streams[stream_id].flush()
            # Audio frames are frequent: keep the log compact.
            if n % 25 and not payload.startswith(b"OggS\x00\x02"):
                record.pop("head")
        self.recorder.write(record)


def run_sniffer(
    host: str, port: int, out_dir: Path, zstd_dict: Path | None, reply_audio: Path | None = None
) -> None:
    recorder = Recorder(out_dir, zstd_dict)
    factory = None
    if reply_audio is not None:
        from .spike_responder import SpikeResponder

        def factory(send):
            return SpikeResponder(
                send, reply_audio, reply_text="Привет! Это ответ от Home Assistant адаптера.",
                asr_text="привет",
            )

    web.run_app(Sniffer(recorder, factory).build_app(), host=host, port=port, print=None)
