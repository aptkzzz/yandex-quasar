"""Phase 0 spike: answer every voice request with a fixed phrase.

Verifies the minimal server-side sequence the station needs to speak:
AsrResult(EndOfUtt) -> StreamControl Close(mic) -> AliceResponse(tts_play_placeholder) -> TtsSpeak + Ogg/Opus -> Close.
Not production code: the real implementation goes to ``uniproxy/`` and ``turns.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from . import aapi

_LOGGER = logging.getLogger(__name__)

SendFn = Callable[[bytes], Awaitable[None]]

ASR_DELAY_AFTER_SPOTTER = 2.5  # station gives up with NO_SPEECH after ~5 s without ASR results
MAX_CHUNK = 32 * 1024


@dataclass
class VoiceTurn:
    message_id: str
    stream_id: int
    request_id: str
    sequence_number: int
    answered: bool = False


def _split_ogg_pages(data: bytes, max_chunk: int = MAX_CHUNK) -> list[bytes]:
    """Group whole Ogg pages into chunks not larger than max_chunk (a single page may exceed it)."""
    chunks: list[bytes] = []
    cur = bytearray()
    pos = 0
    while pos < len(data):
        nseg = data[pos + 26]
        page_len = 27 + nseg + sum(data[pos + 27:pos + 27 + nseg])
        if cur and len(cur) + page_len > max_chunk:
            chunks.append(bytes(cur))
            cur = bytearray()
        cur += data[pos:pos + page_len]
        pos += page_len
    if cur:
        chunks.append(bytes(cur))
    return chunks


def _new_id() -> str:
    return str(uuid.uuid4())


async def synthesize(text: str) -> list[bytes]:
    """Spike TTS: macOS `say` -> ffmpeg -> Ogg/Opus 48 kHz chunks."""
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "tts.aiff"
        opus = Path(tmp) / "tts.opus"
        proc = await asyncio.create_subprocess_exec("say", "-v", "Milena", "-o", str(aiff), text)
        await proc.wait()
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", "48000", "-c:a", "libopus",
            "-b:a", "48k", "-page_duration", "100000", str(opus),
        )
        await proc.wait()
        return _split_ogg_pages(opus.read_bytes())


class SpikeResponder:
    """Per-connection state machine."""

    def __init__(self, send: SendFn, reply_audio: Path, reply_text: str, asr_text: str) -> None:
        self._send = send
        self._reply_chunks = _split_ogg_pages(reply_audio.read_bytes())
        self._reply_text = reply_text
        self._asr_text = asr_text
        self._turns: dict[int, VoiceTurn] = {}
        self._next_server_stream = 2
        self._tasks: set[asyncio.Task] = set()

    async def handle_aapi(self, frame: bytes) -> None:
        msg = aapi.parse_client(frame)
        which = msg.WhichOneof("Message")
        if which == "StreamControl":
            await self._on_stream_control(msg.StreamControl)
            return
        event = msg.Event
        kind = event.WhichOneof("Event")
        ref = event.Header.MessageId
        if kind == "SynchronizeStateRequest":
            out = aapi.TServerMessage()
            out.Event.Header.MessageId = _new_id()
            out.Event.Header.RefMessageId = ref
            out.Event.SynchronizeStateResponse.SessionId = _new_id()
            await self._send_msg(out, "SynchronizeStateResponse")
        elif kind == "LogSpotter":
            out = aapi.TServerMessage()
            out.Event.Header.MessageId = _new_id()
            out.Event.Header.RefMessageId = ref
            out.Event.LogAck.SetInParent()
            await self._send_msg(out, "LogAck")
        elif kind == "RequestStat":
            out = aapi.TServerMessage()
            out.Event.Header.MessageId = _new_id()
            out.Event.Header.RefMessageId = ref
            out.Event.RequestStatAck.SetInParent()
            await self._send_msg(out, "RequestStatAck")
        elif kind == "VoiceInput":
            turn = VoiceTurn(
                message_id=ref,
                stream_id=event.Header.StreamId,
                request_id=event.VoiceInput.Header.RequestId,
                sequence_number=event.VoiceInput.Header.SequenceNumber,
            )
            self._turns[turn.stream_id] = turn
            _LOGGER.info("VoiceInput stream=%s request=%s", turn.stream_id, turn.request_id)
            out = aapi.TServerMessage()
            out.Event.Header.MessageId = _new_id()
            out.Event.Header.RefMessageId = ref
            out.Event.InputStartAck.SetInParent()
            await self._send_msg(out, "InputStartAck")
        elif kind == "TextInput":
            text_input = event.TextInput
            req_event = text_input.Request.Event
            payload = MessageToDict(req_event.Payload) if req_event.HasField("Payload") else {}
            _LOGGER.info(
                "TextInput type=%s name=%s payload=%s", req_event.Type, req_event.Name,
                json.dumps(payload, ensure_ascii=False)[:500],
            )
            tts_text = (
                payload.get("typed_semantic_frame", {}).get("tts_semantic_frame", {}).get("text")
            )
            if tts_text:
                # Boomerang: our pushed @@mm_semantic_frame came back, answer it with speech.
                await self._send_alice_response(
                    ref, text_input.Header.RequestId, text_input.Header.SequenceNumber, tts_text
                )
                await self._send_tts(ref, await synthesize(tts_text))
                return
            # Background server_actions (sync_timezone, player callbacks): empty response stops retries.
            await self._send_alice_response(
                ref, text_input.Header.RequestId, text_input.Header.SequenceNumber, None
            )

    async def push(self, directives: list[dict]) -> None:
        """Send a server-initiated Push with the given directives ({type, name, payload})."""
        out = aapi.TServerMessage()
        out.Event.Header.MessageId = _new_id()
        out.Event.Header.Ack = int(time.time() * 1000)
        push = out.Event.Push
        push.PushIds.append(_new_id())
        push.DeduplicationPushId = _new_id()
        push.AnalyticsMetaInfo.SetInParent()
        for item in directives:
            directive = push.Directives.add()
            directive.Type = item.get("type", "client_action")
            directive.Name = item["name"]
            directive.IsLedSilent = item.get("is_led_silent", False)
            if "directive_type" in item:
                # Typed directive: serialized proto in PayloadRaw (JSON payloads are rejected by protobuf-lite maind).
                directive.DirectiveType = item["directive_type"]
                directive.PayloadRaw = base64.b64decode(item.get("payload_raw_b64", ""))
            else:
                payload = struct_pb2.Struct()
                payload.update(item.get("payload", {}))
                directive.Payload.CopyFrom(payload)
        await self._send_msg(out, f"Push {[d['name'] for d in directives]}")

    async def _on_stream_control(self, ctrl) -> None:
        turn = self._turns.get(ctrl.StreamId)
        action = ctrl.WhichOneof("Action")
        if turn is None:
            return
        if action == "SpotterEnd":
            task = asyncio.create_task(self._answer_later(turn))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif action == "Close":
            _LOGGER.info("mic stream %s closed by station: %s", ctrl.StreamId, ctrl.Close.Reason)

    async def _answer_later(self, turn: VoiceTurn) -> None:
        await asyncio.sleep(ASR_DELAY_AFTER_SPOTTER)
        if turn.answered:
            return
        turn.answered = True

        asr = aapi.TServerMessage()
        asr.Event.Header.MessageId = _new_id()
        asr.Event.Header.RefMessageId = turn.message_id
        asr.Event.AsrResult.EndOfUtt = True
        asr.Event.AsrResult.ResponseCode = 0
        rec = asr.Event.AsrResult.Recognition.add()
        rec.Normalized = self._asr_text
        rec.Confidence = 1.0
        for word in self._asr_text.split():
            w = rec.Words.add()
            w.Value = word
            w.Confidence = 1.0
        await self._send_msg(asr, "AsrResult(EndOfUtt)")

        close = aapi.TServerMessage()
        close.StreamControl.StreamId = turn.stream_id
        close.StreamControl.MessageId = _new_id()
        close.StreamControl.Close.Reason = 0
        await self._send_msg(close, f"Close mic stream {turn.stream_id}")

        await self._send_alice_response(
            turn.message_id, turn.request_id, turn.sequence_number, self._reply_text
        )
        await self._send_tts(turn.message_id, self._reply_chunks)

    async def _send_alice_response(
        self, ref: str, request_id: str, sequence_number: int, text: str | None
    ) -> None:
        out = aapi.TServerMessage()
        out.Event.Header.MessageId = _new_id()
        out.Event.Header.RefMessageId = ref
        resp = out.Event.AliceResponse
        resp.Header.RequestId = request_id
        resp.Header.ResponseId = _new_id()
        resp.Header.SequenceNumber = sequence_number
        resp.Response.SetInParent()
        if text:
            resp.VoiceResponse.OutputSpeech.Text = text
            resp.VoiceResponse.HasVoiceResponse = True
            resp.VoiceResponse.ShouldListen = False
            directive = resp.Response.Directives.add()
            directive.Type = "client_action"
            directive.Name = "tts_play_placeholder"
            directive.IsLedSilent = True
            payload = struct_pb2.Struct()
            payload.update({"channel": "Dialog"})
            directive.Payload.CopyFrom(payload)
        await self._send_msg(out, f"AliceResponse(text={text!r})")

    async def _send_tts(self, ref: str, chunks: list[bytes]) -> None:
        stream_id = self._next_server_stream
        self._next_server_stream += 2
        speak = aapi.TServerMessage()
        speak.Event.Header.MessageId = _new_id()
        speak.Event.Header.RefMessageId = ref
        speak.Event.Header.StreamId = stream_id
        speak.Event.TtsSpeak.Format = "audio/opus"
        await self._send_msg(speak, f"TtsSpeak stream={stream_id}")
        for chunk in chunks:
            await self._send(aapi.make_stream_frame(stream_id, chunk))
        close = aapi.TServerMessage()
        close.StreamControl.StreamId = stream_id
        close.StreamControl.MessageId = _new_id()
        close.StreamControl.Close.Reason = 0
        await self._send_msg(close, f"Close tts stream {stream_id}")

    async def _send_msg(self, msg, label: str) -> None:
        _LOGGER.info("<- %s", label)
        await self._send(aapi.encode(msg))
