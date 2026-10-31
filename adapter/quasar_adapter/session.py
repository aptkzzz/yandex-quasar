"""One uniproxy (AAPI) connection of a station: voice turns, server-initiated turns, media control.

Protocol facts this code relies on are documented in docs/device-notes.md and docs/station-protocol.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web
from google.protobuf.json_format import MessageToDict

from . import aapi
from .audio import MicDecoder, OpusTranscoder, pcm_level_db
from .media import resolve_media_url
from .station import WAKE_WORDS

if TYPE_CHECKING:
    from .hub import Hub
    from .station import Station

_LOGGER = logging.getLogger(__name__)

MEDIA_CALLBACK_PREFIX = "qa_media_"
ANNOUNCE_TOKEN_PREFIX = "qa-turn:"
BOOMERANG_TIMEOUT = 5.0
PARTIAL_PLACEHOLDER = "…"

_turn_seq = itertools.count(1)


class CommandError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class Turn:
    """A dialog turn: started by the station (voice) or by us (announce)."""

    turn_id: str
    kind: str  # "voice" | "announce"
    seq: int = field(default_factory=lambda: next(_turn_seq))
    message_id: str = ""  # MessageId of the station event we answer (RefMessageId of our replies)
    request_id: str = ""
    sequence_number: int = 0
    mic_stream_id: int | None = None
    phase: str = "listening"  # listening | recognized | speaking | done
    decoder: MicDecoder | None = None
    preroll: list[bytes] = field(default_factory=list)
    forwarding: bool = False
    speech_seen: bool = False
    skip_bytes: int = 0
    asr_sent: bool = False
    debug_pcm: bytearray | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)
    playback_done: asyncio.Future | None = None
    # announce-only
    media_urls: list[str] = field(default_factory=list)
    text: str = ""
    listen: bool = False


class StationSession:
    def __init__(self, hub: Hub, station: Station, ws: web.WebSocketResponse) -> None:
        self.hub = hub
        self.station = station
        self.ws = ws
        self.cfg = hub.config
        self._send_lock = asyncio.Lock()
        self._next_stream = 2
        self._turns: dict[str, Turn] = {}
        self._voice: Turn | None = None
        self._announce_by_token: dict[str, Turn] = {}
        self._media_is_stream = False
        self._closed = False

    # ---- plumbing -------------------------------------------------------------------------------------------

    async def run(self) -> None:
        async for msg in self.ws:
            if msg.type.name != "BINARY":
                continue
            data: bytes = msg.data
            try:
                if aapi.is_aapi(data):
                    await self._on_message(aapi.parse_client(data))
                else:
                    stream_id, payload = aapi.split_stream_frame(data)
                    await self._on_audio(stream_id, payload)
            except Exception:
                _LOGGER.exception("[%s] failed to handle frame", self.station.device_id)

    async def close(self) -> None:
        self._closed = True
        for turn in list(self._turns.values()):
            self._finish(turn, emit_stop="disconnected")

    async def _send(self, msg: Any) -> None:
        await self._send_raw(aapi.encode(msg))

    async def _send_raw(self, data: bytes) -> None:
        if self._closed or self.ws.closed:
            return
        async with self._send_lock:
            await self.ws.send_bytes(data)

    def _spawn(self, turn: Turn | None, coro: Awaitable, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        if turn is not None:
            turn.tasks.add(task)
            task.add_done_callback(turn.tasks.discard)
        else:
            self.hub.track(task)
        return task

    def _alloc_stream(self) -> int:
        sid = self._next_stream
        self._next_stream += 2
        return sid

    # ---- station → adapter ----------------------------------------------------------------------------------

    async def _on_message(self, msg: Any) -> None:
        if msg.WhichOneof("Message") == "StreamControl":
            await self._on_stream_control(msg.StreamControl)
            return
        event = msg.Event
        kind = event.WhichOneof("Event")
        ref = event.Header.MessageId
        if kind == "SynchronizeStateRequest":
            await self._send(aapi.synchronize_state_response(ref, str(uuid.uuid4())))
        elif kind == "LogSpotter":
            await self._send(aapi.log_ack(ref))
        elif kind == "RequestStat":
            await self._send(aapi.request_stat_ack(ref))
            self._on_request_stat(event.RequestStat)
        elif kind == "VoiceInput":
            await self._on_voice_input(event)
        elif kind == "TextInput":
            await self._on_text_input(event)
        elif kind == "CancelRequest":
            self._on_cancel(event.CancelRequest.RequestMessageId)
        else:
            _LOGGER.debug("[%s] ignored event %s", self.station.device_id, kind)

    def _update_device_state(self, request: Any) -> None:
        state = aapi.parse_device_state(request.DeviceStateRaw)
        if state is not None and self.station.apply_device_state(state):
            self.hub.emit_state(self.station)

    async def _on_voice_input(self, event: Any) -> None:
        vi = event.VoiceInput
        self._update_device_state(vi.Request)
        if self._voice is not None:
            self._finish(self._voice, emit_stop="cancel")
        turn = Turn(
            turn_id=str(uuid.uuid4()), kind="voice", message_id=event.Header.MessageId,
            request_id=vi.Header.RequestId, sequence_number=vi.Header.SequenceNumber,
            mic_stream_id=event.Header.StreamId,
        )
        if self.cfg.debug_audio_dir:
            turn.debug_pcm = bytearray()
        self._turns[turn.turn_id] = turn
        self._voice = turn
        await self._send(aapi.input_start_ack(turn.message_id))
        wake_word = vi.SpotterPhrase.capitalize() if vi.Request.ActivationType == "spotter" else None
        _LOGGER.info(
            "[%s] voice turn %s (activation=%s)", self.station.device_id, turn.turn_id,
            vi.Request.ActivationType or "?",
        )
        if not self.hub.has_ha_clients():
            _LOGGER.warning("[%s] no Home Assistant connected, dropping voice request", self.station.device_id)
            await self._respond_empty(turn)
            return
        self._set_alice_state("listening")
        self.hub.emit(self.station, "voice.start", {
            "turn_id": turn.turn_id, "turn_seq": turn.seq, "wake_word": wake_word,
            "reason": "wake_word" if wake_word else "follow_up",
        })
        self._spawn(turn, self._preroll_timeout(turn), "preroll")
        self._spawn(turn, self._partials(turn), "partials")
        self._spawn(turn, self._utterance_timeout(turn), "utterance-timeout")

    async def _on_audio(self, stream_id: int, payload: bytes) -> None:
        turn = self._voice
        if turn is None or turn.mic_stream_id != stream_id or turn.phase != "listening":
            return
        if not turn.forwarding:
            turn.preroll.append(payload)
            return
        self._forward(turn, payload)

    def _forward(self, turn: Turn, payload: bytes) -> None:
        assert turn.decoder is not None
        pcm = turn.decoder.feed(payload)
        if turn.skip_bytes and pcm:
            skipped = min(turn.skip_bytes, len(pcm))
            turn.skip_bytes -= skipped
            pcm = pcm[skipped:]
        if pcm:
            if not turn.speech_seen and pcm_level_db(pcm) > self.cfg.speech_threshold_db:
                turn.speech_seen = True
            if turn.debug_pcm is not None:
                turn.debug_pcm += pcm
            self.hub.emit_audio(turn.seq, pcm)

    async def _preroll_timeout(self, turn: Turn) -> None:
        # Wake-word activations push ~2 s of buffered audio together with SpotterEnd. Without SpotterEnd
        # (follow-up listening) everything is the command, so start forwarding after a short wait.
        await asyncio.sleep(self.cfg.preroll_timeout)
        if not turn.forwarding and turn.phase == "listening":
            self._start_forwarding(turn, keep_preroll=True)

    def _start_forwarding(self, turn: Turn, keep_preroll: bool) -> None:
        turn.forwarding = True
        turn.decoder = MicDecoder(self.cfg.mic_gain_db)
        buffered, turn.preroll = turn.preroll, []
        if keep_preroll:
            for chunk in buffered:
                self._forward(turn, chunk)

    async def _partials(self, turn: Turn) -> None:
        # The station closes the mic with NO_SPEECH ~5 s after SpotterEnd unless it sees a NON-EMPTY partial,
        # so once our level detector hears speech we report a placeholder partial until HA decides.
        while turn.phase == "listening":
            await asyncio.sleep(self.cfg.partial_interval)
            if turn.phase == "listening":
                text = PARTIAL_PLACEHOLDER if turn.speech_seen else ""
                await self._send(aapi.asr_result(turn.message_id, text, end_of_utt=False))

    async def _utterance_timeout(self, turn: Turn) -> None:
        await asyncio.sleep(self.cfg.max_utterance)
        if turn.phase == "listening":
            _LOGGER.info("[%s] turn %s: utterance timeout", self.station.device_id, turn.turn_id)
            self.hub.emit(self.station, "voice.stop", {"turn_id": turn.turn_id, "reason": "timeout"})
            await self._respond_empty(turn)

    async def _on_stream_control(self, ctrl: Any) -> None:
        turn = self._voice
        if turn is None or turn.mic_stream_id != ctrl.StreamId:
            return
        action = ctrl.WhichOneof("Action")
        if action == "SpotterEnd":
            if turn.phase == "listening" and not turn.forwarding:
                self._start_forwarding(turn, keep_preroll=False)
                turn.skip_bytes = self.cfg.spotter_tail_ms * 16 * 2  # 16 kHz s16le
        elif action == "Close" and turn.phase == "listening":
            reason = ctrl.Close.Reason
            _LOGGER.info("[%s] mic closed by station, reason=%s", self.station.device_id, reason)
            if reason == aapi.CLOSE_CANCEL:
                self.hub.emit(self.station, "voice.stop", {"turn_id": turn.turn_id, "reason": "cancel"})
                self._finish(turn)
            elif reason == aapi.CLOSE_NO_SPEECH:
                self.hub.emit(self.station, "voice.stop", {"turn_id": turn.turn_id, "reason": "no_speech"})
                await self._respond_empty(turn)
            else:
                # Station stopped recording; HA finishes STT with what it has and answers as usual.
                turn.phase = "recognized"
                self.hub.emit(self.station, "voice.stop", {"turn_id": turn.turn_id, "reason": "end"})

    def _on_cancel(self, request_message_id: str) -> None:
        for turn in list(self._turns.values()):
            if turn.message_id == request_message_id:
                _LOGGER.info("[%s] station cancelled turn %s", self.station.device_id, turn.turn_id)
                self._finish(turn, emit_stop="cancel")

    def _on_request_stat(self, stat: Any) -> None:
        for turn in self._turns.values():
            if turn.message_id == stat.RequestMessageId and turn.phase == "speaking":
                if turn.playback_done and not turn.playback_done.done():
                    turn.playback_done.set_result(stat.Timestamps.OnSoundPlayerEndTime)

    async def _on_text_input(self, event: Any) -> None:
        ti = event.TextInput
        req_event = ti.Request.Event
        self._update_device_state(ti.Request)
        payload = MessageToDict(req_event.Payload) if req_event.HasField("Payload") else {}
        name = req_event.Name
        ref = event.Header.MessageId

        token = payload.get("typed_semantic_frame", {}).get("tts_semantic_frame", {}).get("text", "")
        turn = self._announce_by_token.pop(token, None) if token.startswith(ANNOUNCE_TOKEN_PREFIX) else None
        if turn is not None:
            turn.message_id = ref
            turn.request_id = ti.Header.RequestId
            turn.sequence_number = ti.Header.SequenceNumber
            self._spawn(turn, self._speak(turn, turn.text, turn.media_urls, turn.listen), "announce")
            return

        if name.startswith(MEDIA_CALLBACK_PREFIX):
            _LOGGER.info("[%s] media callback %s", self.station.device_id, name)
            self._on_media_callback(name.removeprefix(MEDIA_CALLBACK_PREFIX), payload)
        else:
            _LOGGER.debug("[%s] server_action %s", self.station.device_id, name)
        # Answer every server_action, otherwise the station retries/timeouts after 15 s.
        await self._send(aapi.alice_response(ref, ti.Header.RequestId, ti.Header.SequenceNumber))

    # ---- turn completion ------------------------------------------------------------------------------------

    def _finish(self, turn: Turn, emit_stop: str | None = None) -> None:
        if turn.phase == "done":
            return
        if emit_stop:
            self.hub.emit(self.station, "voice.stop", {"turn_id": turn.turn_id, "reason": emit_stop})
        turn.phase = "done"
        current = asyncio.current_task()
        for task in list(turn.tasks):
            if task is not current:
                task.cancel()
        if turn.playback_done and not turn.playback_done.done():
            turn.playback_done.cancel()
        self._turns.pop(turn.turn_id, None)
        if self._voice is turn:
            self._voice = None
        if not self._turns:
            self._set_alice_state("idle")

    def _dump_debug_audio(self, turn: Turn) -> None:
        if not turn.debug_pcm:
            return
        path = Path(self.cfg.debug_audio_dir) / f"{turn.turn_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(turn.debug_pcm))
        turn.debug_pcm = None
        _LOGGER.info("[%s] turn audio saved to %s", self.station.device_id, path)

    async def _end_recognition(self, turn: Turn, text: str) -> None:
        if turn.asr_sent or turn.mic_stream_id is None:
            return
        turn.asr_sent = True
        self._dump_debug_audio(turn)
        if turn.phase == "listening":
            turn.phase = "recognized"
        await self._send(aapi.asr_result(turn.message_id, text, end_of_utt=True))
        await self._send(aapi.stream_close(turn.mic_stream_id))
        self._set_alice_state("thinking")

    async def _respond_empty(self, turn: Turn, directives: list[dict] | None = None) -> None:
        await self._end_recognition(turn, "")
        await self._send(aapi.alice_response(
            turn.message_id, turn.request_id, turn.sequence_number, directives=directives,
        ))
        self._finish(turn)

    def _error_sound(self) -> list[dict]:
        if not self.cfg.error_sound:
            return []
        try:
            return [aapi.local_sound(self.cfg.error_sound)]
        except ValueError:
            _LOGGER.warning("Unknown error_sound %r, see EStoredSound", self.cfg.error_sound)
            return []

    async def _speak(self, turn: Turn, text: str, urls: list[str], listen: bool) -> None:
        """AliceResponse + TtsSpeak + Ogg/Opus stream, then wait for the station to finish playing."""
        await self._end_recognition(turn, "")
        has_voice = bool(urls)
        await self._send(aapi.alice_response(
            turn.message_id, turn.request_id, turn.sequence_number,
            text=text, has_voice=has_voice, should_listen=listen,
        ))
        if not has_voice:
            self._playback_finished(turn)
            return
        turn.phase = "speaking"
        turn.playback_done = asyncio.get_running_loop().create_future()
        self._set_alice_state("speaking")
        stream_id = self._alloc_stream()
        transcoder = OpusTranscoder(urls, ffmpeg=self.cfg.ffmpeg)
        started = time.monotonic()
        error = None
        try:
            await self._send(aapi.tts_speak(turn.message_id, stream_id))
            async for chunk in transcoder.chunks():
                await self._send_raw(aapi.make_stream_frame(stream_id, chunk))
        except Exception as err:  # noqa: BLE001 - report to HA, never break the session
            _LOGGER.warning("[%s] TTS streaming failed: %s", self.station.device_id, err)
            error = str(err)
        finally:
            await self._send(aapi.stream_close(stream_id))
        if error:
            self._playback_finished(turn, error=error)
            return
        # Station reports the end via RequestStat; fall back to audio duration if it never arrives.
        remaining = max(0.0, transcoder.duration - (time.monotonic() - started)) + self.cfg.playback_grace
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(turn.playback_done), timeout=remaining)
        self._playback_finished(turn)

    def _playback_finished(self, turn: Turn, error: str | None = None) -> None:
        data: dict[str, Any] = {"turn_id": turn.turn_id}
        if error:
            data["error"] = error
        self.hub.emit(self.station, "voice.playback_finished", data)
        self._finish(turn)

    def _set_alice_state(self, value: str) -> None:
        if self.station.state.alice_state != value:
            self.station.state.alice_state = value
            self.hub.emit_state(self.station)

    # ---- HA commands ----------------------------------------------------------------------------------------

    async def command(self, type_: str, data: dict) -> dict:
        handler: Callable[[dict], Awaitable[dict]] | None = getattr(
            self, "_cmd_" + type_.replace(".", "_"), None
        )
        if handler is None:
            raise CommandError("unknown_command", type_)
        return await handler(data)

    def _turn(self, data: dict) -> Turn:
        turn = self._turns.get(data.get("turn_id", ""))
        if turn is None:
            raise CommandError("turn_closed")
        return turn

    async def _cmd_voice_stt_end(self, data: dict) -> dict:
        await self._end_recognition(self._turn(data), data.get("text", ""))
        return {}

    async def _cmd_voice_respond(self, data: dict) -> dict:
        turn = self._turn(data)
        urls = [data["tts_url"]] if data.get("tts_url") else []
        self._spawn(turn, self._speak(turn, data.get("text", ""), urls, bool(data.get("listen"))), "speak")
        return {}

    async def _cmd_voice_error(self, data: dict) -> dict:
        turn = self._turn(data)
        _LOGGER.info("[%s] HA pipeline error: %s", self.station.device_id, data.get("message"))
        await self._respond_empty(turn, self._error_sound())
        return {}

    async def _cmd_voice_abort(self, data: dict) -> dict:
        turn = self._turn(data)
        await self._respond_empty(turn)
        return {}

    async def _cmd_voice_announce(self, data: dict) -> dict:
        urls = [u for u in (data.get("preannounce_url"), data.get("media_url")) if u]
        if not urls:
            raise CommandError("invalid", "media_url is required")
        turn = Turn(
            turn_id=data.get("turn_id") or str(uuid.uuid4()), kind="announce", phase="recognized",
            media_urls=urls, text=data.get("text", ""), listen=bool(data.get("listen")),
        )
        token = ANNOUNCE_TOKEN_PREFIX + turn.turn_id
        self._turns[turn.turn_id] = turn
        self._announce_by_token[token] = turn
        await self._send(aapi.push([aapi.directive(
            "@@mm_semantic_frame",
            {"typed_semantic_frame": {"tts_semantic_frame": {"text": token}}},
            type_="server_action", led_silent=True,
        )]))
        self._spawn(turn, self._boomerang_timeout(turn, token), "boomerang-timeout")
        return {"turn_id": turn.turn_id}

    async def _boomerang_timeout(self, turn: Turn, token: str) -> None:
        await asyncio.sleep(BOOMERANG_TIMEOUT)
        if self._announce_by_token.pop(token, None) is not None:
            _LOGGER.warning("[%s] announce %s: station did not answer", self.station.device_id, turn.turn_id)
            self._playback_finished(turn, error="station_timeout")

    async def _push(self, *directives: dict) -> None:
        await self._send(aapi.push(list(directives)))

    async def _cmd_media_play(self, data: dict) -> dict:
        url, is_stream = await resolve_media_url(self.hub.http, data["url"])
        meta = data.get("metadata") or {}
        offset = float(data.get("offset") or 0)
        stream_id = str(uuid.uuid4())
        callbacks = {
            # Same shape as the phase 0 experiment that is confirmed to come back (docs/device-notes.md).
            f"on_{event}": {
                "name": MEDIA_CALLBACK_PREFIX + event, "type": "server_action", "ignore_answer": True,
                "payload": {"stream_id": stream_id},
            }
            for event in ("started", "stopped", "finished", "failed")
        }
        await self._push(aapi.directive("audio_play", {
            "background_mode": "Ducking",
            "screen_type": "Music",
            "stream": {
                "id": stream_id, "type": "Track", "url": url, "format": "MP3",
                "offset_ms": int(offset * 1000),
            },
            "metadata": {
                "title": meta.get("title") or "", "subtitle": meta.get("artist") or "",
                "art_image_url": meta.get("image") or "",
            },
            "callbacks": callbacks,
        }))
        player = self.station.state.player
        player.state = "buffering"
        player.url = data["url"]
        player.stream_id = stream_id
        player.title = meta.get("title")
        player.artist = meta.get("artist")
        player.image = meta.get("image")
        player.duration = meta.get("duration")
        player.position = offset
        player.position_ts = time.time()
        self._media_is_stream = is_stream
        self.hub.emit_state(self.station)
        return {"stream_id": stream_id}

    def _on_media_callback(self, event: str, payload: dict) -> None:
        player = self.station.state.player
        if payload.get("stream_id") != player.stream_id:
            return  # callback of a replaced track
        if event == "started":
            player.state = "playing"
        elif event in ("stopped", "finished") and player.state != "paused":
            player.state = "idle"
        elif event == "failed":
            player.state = "idle"
        self.hub.emit(self.station, "media.event", {"event": event, "url": player.url})
        self.hub.emit_state(self.station)

    def _current_position(self) -> float:
        player = self.station.state.player
        if player.state == "playing":
            return player.position + (time.time() - player.position_ts)
        return player.position

    async def _cmd_media_pause(self, data: dict) -> dict:
        player = self.station.state.player
        position = self._current_position()
        await self._push(aapi.directive("player_pause", {"smooth": True}))
        player.state = "paused"
        player.position = position
        player.position_ts = time.time()
        self.hub.emit_state(self.station)
        return {}

    async def _cmd_media_resume(self, data: dict) -> dict:
        # player_continue does not work for audio_play on this firmware: restart the URL at the saved position.
        player = self.station.state.player
        if not player.url:
            raise CommandError("nothing_to_resume")
        return await self._cmd_media_play({
            "url": player.url,
            "offset": 0 if self._media_is_stream else player.position,
            "metadata": {"title": player.title, "artist": player.artist, "image": player.image,
                         "duration": player.duration},
        })

    async def _cmd_media_stop(self, data: dict) -> dict:
        await self._push(aapi.directive("player_pause", {"smooth": False}))
        player = self.station.state.player
        player.state = "idle"
        player.url = None
        player.stream_id = None
        player.position = 0.0
        self.hub.emit_state(self.station)
        return {}

    async def _cmd_media_seek(self, data: dict) -> dict:
        player = self.station.state.player
        if not player.url or self._media_is_stream:
            raise CommandError("not_seekable")
        return await self._cmd_media_play({
            "url": player.url, "offset": float(data["position"]),
            "metadata": {"title": player.title, "artist": player.artist, "image": player.image,
                         "duration": player.duration},
        })

    async def _cmd_volume_set(self, data: dict) -> dict:
        level = max(0.0, min(1.0, float(data["level"])))
        new_level = round(level * 10)
        await self._push(aapi.directive("sound_set_level", {"new_level": new_level}))
        self.station.state.volume = new_level / 10
        self.hub.emit_state(self.station)
        return {}

    async def _cmd_volume_step(self, data: dict) -> dict:
        name = "sound_louder" if data.get("direction") == "up" else "sound_quiter"
        await self._push(aapi.directive(name))
        volume = self.station.state.volume
        if volume is not None:
            delta = 0.1 if data.get("direction") == "up" else -0.1
            self.station.state.volume = round(max(0.0, min(1.0, volume + delta)), 2)
            self.hub.emit_state(self.station)
        return {}

    async def _cmd_volume_mute(self, data: dict) -> dict:
        muted = bool(data["muted"])
        await self._push(aapi.directive("sound_mute" if muted else "sound_unmute"))
        self.station.state.muted = muted
        self.hub.emit_state(self.station)
        return {}

    async def _cmd_station_get(self, data: dict) -> dict:
        return self.station.info()

    async def _cmd_config_wake_word(self, data: dict) -> dict:
        """Switch the on-device spotter: served as account_config.spotter, applied via push_update_config."""
        wake_word = data["wake_word"]
        if wake_word not in WAKE_WORDS:
            raise CommandError("invalid", f"unsupported wake word {wake_word!r}")
        self.station.wake_word = wake_word
        self.hub.save_registry()
        await self._push(aapi.directive("push_update_config"))
        _LOGGER.info("[%s] wake word set to %s", self.station.device_id, wake_word)
        return self.station.info()

    async def _cmd_debug_sound(self, data: dict) -> dict:
        """Experiments: play a stored station sound by EStoredSound name."""
        try:
            await self._push(aapi.local_sound(data["sound"]))
        except ValueError as err:
            raise CommandError("invalid", f"unknown sound {data['sound']!r}") from err
        return {}

    async def _cmd_debug_push(self, data: dict) -> dict:
        """Experiments: push raw directives [{name, payload, type?}] without restarting the adapter."""
        await self._push(*(
            aapi.directive(d["name"], d.get("payload"), type_=d.get("type", "client_action"))
            for d in data["directives"]
        ))
        return {}
