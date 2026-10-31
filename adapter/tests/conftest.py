"""Test helpers: a fake station speaking AAPI and a fake Home Assistant client."""

from __future__ import annotations

import asyncio
import json
import struct
import subprocess

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from google.protobuf import struct_pb2

from quasar_adapter import aapi
from quasar_adapter.audio import split_ogg_pages
from quasar_adapter.config import Config
from quasar_adapter.server import build_app

DEVICE_ID = "TESTDEVICE0001"


def make_opus(seconds: float, freq: int) -> list[bytes]:
    """Ogg/Opus 16 kHz mono sine, split into ~20 ms pages like the station sends."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=16000:duration={seconds}",
         "-c:a", "libopus", "-b:a", "24k", "-page_duration", "20000", "-f", "ogg", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    pages, _ = split_ogg_pages(out)
    return pages


@pytest.fixture
def tts_file(tmp_path) -> str:
    """A 2 s MP3 standing in for the TTS URL HA would hand out."""
    path = tmp_path / "tts.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame",
         str(path)],
        check=True,
    )
    return str(path)


class FakeStation:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws

    async def send(self, msg) -> None:
        await self.ws.send_bytes(aapi.encode(msg))

    async def send_audio(self, stream_id: int, page: bytes) -> None:
        await self.ws.send_bytes(aapi.make_stream_frame(stream_id, page))

    async def recv(self, timeout: float = 5.0):
        """Next AAPI message as (message, None) or stream frame as (None, (stream_id, payload))."""
        msg = await self.ws.receive(timeout=timeout)
        assert msg.type == aiohttp.WSMsgType.BINARY, msg
        if aapi.is_aapi(msg.data):
            return aapi.parse_server(msg.data), None
        return None, aapi.split_stream_frame(msg.data)

    async def expect_event(self, kind: str, timeout: float = 5.0, skip_partials: bool = True):
        while True:
            msg, _ = await self.recv(timeout)
            if msg is None:
                continue
            if msg.WhichOneof("Message") == "Event":
                got = msg.Event.WhichOneof("Event")
                if skip_partials and got == "AsrResult" and not msg.Event.AsrResult.EndOfUtt:
                    continue
                if got == kind:
                    return msg
                raise AssertionError(f"expected {kind}, got {got}")
            if kind == "StreamControl":
                return msg
            raise AssertionError(f"expected {kind}, got StreamControl {msg}")

    @staticmethod
    def event(message_id: str, stream_id: int | None = None):
        msg = aapi.TClientMessage()
        msg.Event.Header.MessageId = message_id
        if stream_id is not None:
            msg.Event.Header.StreamId = stream_id
        return msg

    def voice_input(self, message_id: str, stream_id: int, spotter: bool = True):
        msg = self.event(message_id, stream_id)
        vi = msg.Event.VoiceInput
        vi.Header.RequestId = "req-" + message_id
        vi.Header.SequenceNumber = 1
        vi.Request.Event.Type = "voice_input"
        if spotter:
            vi.Request.ActivationType = "spotter"
            vi.SpotterPhrase = "алиса"
        return msg

    @staticmethod
    def stream_control(stream_id: int, action: str, reason: int = 0):
        msg = aapi.TClientMessage()
        msg.StreamControl.StreamId = stream_id
        msg.StreamControl.MessageId = aapi.new_id()
        if action == "SpotterEnd":
            msg.StreamControl.SpotterEnd.SetInParent()
        else:
            msg.StreamControl.Close.Reason = reason
        return msg

    def text_input_server_action(self, message_id: str, name: str, payload: dict):
        msg = self.event(message_id)
        ti = msg.Event.TextInput
        ti.Header.RequestId = "req-" + message_id
        ti.Request.Event.Type = 6  # server_action
        ti.Request.Event.Name = name
        st = struct_pb2.Struct()
        st.update(payload)
        ti.Request.Event.Payload.CopyFrom(st)
        return msg

    def request_stat(self, message_id: str, request_message_id: str):
        msg = self.event(message_id)
        msg.Event.RequestStat.RequestMessageId = request_message_id
        return msg


class FakeHA:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self._id = 0
        self.events: asyncio.Queue = asyncio.Queue()
        self.audio: asyncio.Queue = asyncio.Queue()
        self.results: dict[int, asyncio.Future] = {}
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data["type"] == "result":
                    self.results.pop(data["id"]).set_result(data)
                else:
                    await self.events.put(data)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                kind, seq = struct.unpack(">BI", msg.data[:5])
                await self.audio.put((kind, seq, msg.data[5:]))

    async def request(self, type_: str, station: str | None = None, data: dict | None = None) -> dict:
        self._id += 1
        fut = asyncio.get_running_loop().create_future()
        self.results[self._id] = fut
        await self.ws.send_json({"type": type_, "id": self._id, "station": station, "data": data or {}})
        return await asyncio.wait_for(fut, 5)

    async def expect(self, type_: str, timeout: float = 5.0) -> dict:
        while True:
            event = await asyncio.wait_for(self.events.get(), timeout)
            if event["type"] == type_:
                return event
            if event["type"] not in ("station.state", "station.added", "media.event"):
                raise AssertionError(f"expected {type_}, got {event}")


@pytest.fixture
async def adapter(tmp_path):
    cfg = Config(data_dir=tmp_path, api_token="secret", preroll_timeout=0.3, partial_interval=0.2, glagol_client=False, sendspin_enabled=False,
                 max_utterance=5.0, playback_grace=1.0)
    server = TestServer(build_app(cfg, zeroconf=False))
    await server.start_server()
    async with aiohttp.ClientSession() as http:
        yield server, http
    await server.close()


@pytest.fixture
async def ha(adapter):
    server, http = adapter
    ws = await http.ws_connect(server.make_url("/ha"))
    await ws.send_json({"type": "hello", "id": 0, "data": {"token": "secret", "api": 1}})
    hello = await ws.receive_json(timeout=5)
    assert hello["ok"], hello
    client = FakeHA(ws)
    yield client
    await ws.close()


@pytest.fixture
async def station(adapter, ha):
    server, http = adapter
    ws = await http.ws_connect(server.make_url("/uni.ws"), headers={
        "X-UPRX-DEVICE-ID": DEVICE_ID, "X-UPRX-DEVICE-MODEL": "cucumber", "X-UPRX-FIRMWARE": "0.369.test",
    })
    fake = FakeStation(ws)
    added = await ha.expect("station.added")
    assert added["data"]["name"] == "Яндекс Станция Миди"
    yield fake
    await ws.close()
