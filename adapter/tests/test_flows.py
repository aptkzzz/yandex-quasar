"""End-to-end flows: fake station <-> adapter <-> fake HA."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from google.protobuf.json_format import MessageToDict

from quasar_adapter import aapi

from .conftest import DEVICE_ID, make_opus


async def test_bad_token_rejected(adapter):
    server, http = adapter
    ws = await http.ws_connect(server.make_url("/ha"))
    await ws.send_json({"type": "hello", "id": 1, "data": {"token": "wrong"}})
    reply = await ws.receive_json(timeout=5)
    assert reply["ok"] is False and reply["error"]["code"] == "invalid_auth"


async def test_service_events(station):
    msg = station.event("sync-1")
    msg.Event.SynchronizeStateRequest.SetInParent()
    await station.send(msg)
    reply = await station.expect_event("SynchronizeStateResponse")
    assert reply.Event.Header.RefMessageId == "sync-1"

    stat = station.request_stat("stat-1", "unknown")
    await station.send(stat)
    reply = await station.expect_event("RequestStatAck")
    assert reply.Event.Header.RefMessageId == "stat-1"


async def _voice_until_recognized(station, ha, message_id="voice-1"):
    await station.send(station.voice_input(message_id, 1))
    await station.expect_event("InputStartAck")
    start = await ha.expect("voice.start")
    assert start["station"] == DEVICE_ID
    assert start["data"]["wake_word"] == "Алиса"

    # Pre-roll with the wake word arrives in a burst together with SpotterEnd and must not reach HA.
    for page in make_opus(1.0, 300):
        await station.send_audio(1, page)
    await station.send(station.stream_control(1, "SpotterEnd"))
    for page in make_opus(1.0, 600):
        await station.send_audio(1, page)

    # 1 s after SpotterEnd; continuous sound has no wake-word tail to cut (see test_audio.py).
    pcm = bytearray()
    while len(pcm) < 16000 * 2 * 0.9:
        kind, seq, chunk = await asyncio.wait_for(ha.audio.get(), 5)
        assert kind == 1 and seq == start["data"]["turn_seq"]
        pcm += chunk
    await asyncio.sleep(0.3)
    while not ha.audio.empty():
        pcm += (await ha.audio.get())[2]
    assert len(pcm) <= 16000 * 2 * 1.05, "pre-roll leaked into HA audio"
    return start["data"]["turn_id"]


async def test_voice_turn(station, ha, tts_file):
    turn_id = await _voice_until_recognized(station, ha)

    assert (await ha.request("voice.stt_end", DEVICE_ID, {"turn_id": turn_id, "text": "включи свет"}))["ok"]
    asr = await station.expect_event("AsrResult")
    assert asr.Event.AsrResult.EndOfUtt
    assert asr.Event.AsrResult.Recognition[0].Normalized == "включи свет"
    close, _ = await station.recv()
    assert close.StreamControl.StreamId == 1

    reply = await ha.request("voice.respond", DEVICE_ID, {
        "turn_id": turn_id, "text": "Включаю", "tts_url": tts_file, "listen": True,
    })
    assert reply["ok"], reply
    resp = await station.expect_event("AliceResponse")
    alice = resp.Event.AliceResponse
    assert resp.Event.Header.RefMessageId == "voice-1"
    assert alice.Header.RequestId == "req-voice-1"
    assert alice.VoiceResponse.OutputSpeech.Text == "Включаю"
    assert alice.VoiceResponse.ShouldListen
    assert [d.Name for d in alice.Response.Directives] == ["tts_play_placeholder"]

    speak = await station.expect_event("TtsSpeak")
    tts_stream = speak.Event.Header.StreamId
    audio = bytearray()
    while True:
        msg, frame = await station.recv()
        if frame:
            assert frame[0] == tts_stream
            audio += frame[1]
        else:
            assert msg.StreamControl.StreamId == tts_stream
            break
    assert audio.startswith(b"OggS") and len(audio) > 5000

    await station.send(station.request_stat("stat-2", "voice-1"))
    finished = await ha.expect("voice.playback_finished")
    assert finished["data"] == {"turn_id": turn_id}

    closed = await ha.request("voice.respond", DEVICE_ID, {"turn_id": turn_id, "text": "x"})
    assert closed["error"]["code"] == "turn_closed"


async def test_pipeline_error_plays_station_sound(station, ha):
    turn_id = await _voice_until_recognized(station, ha, "voice-err")
    assert (await ha.request("voice.error", DEVICE_ID, {"turn_id": turn_id, "code": "stt-stream-failed"}))["ok"]
    assert (await station.expect_event("AsrResult")).Event.AsrResult.EndOfUtt
    await station.recv()  # mic stream close
    resp = await station.expect_event("AliceResponse")
    directives = resp.Event.AliceResponse.Response.Directives
    assert [d.Name for d in directives] == ["local_audio_file_play"]
    assert directives[0].DirectiveType == aapi.LOCAL_AUDIO_FILE_PLAY_TYPE
    sound = aapi.audio_file_player_pb2.TAudioFilePlayerCapability.TLocalAudioFilePlayDirective()
    sound.ParseFromString(directives[0].PayloadRaw)
    assert sound.SoundToPlay == aapi._AudioFilePlayer.EStoredSound.Value("SpotterCancel")


async def test_silent_ha_times_out_with_error_sound(adapter, station, ha):
    server, _http = adapter
    from quasar_adapter.server import HUB_KEY

    server.app[HUB_KEY].config.ha_response_timeout = 0.5
    turn_id = await _voice_until_recognized(station, ha, "voice-slow")
    assert (await ha.request("voice.stt_end", DEVICE_ID, {"turn_id": turn_id, "text": "включи рок"}))["ok"]
    assert (await station.expect_event("AsrResult")).Event.AsrResult.EndOfUtt
    await station.recv()  # mic stream close
    resp = await station.expect_event("AliceResponse", timeout=3)
    assert [d.Name for d in resp.Event.AliceResponse.Response.Directives] == ["local_audio_file_play"]
    late = await ha.request("voice.respond", DEVICE_ID, {"turn_id": turn_id, "text": "Включаю"})
    assert late["error"]["code"] == "turn_closed"


async def test_station_cancel(station, ha):
    turn_id = await _voice_until_recognized(station, ha)
    await station.send(station.stream_control(1, "Close", aapi.CLOSE_CANCEL))
    stop = await ha.expect("voice.stop")
    assert stop["data"] == {"turn_id": turn_id, "reason": "cancel"}


async def test_follow_up_without_spotter(station, ha):
    await station.send(station.voice_input("voice-f", 3, spotter=False))
    await station.expect_event("InputStartAck")
    start = await ha.expect("voice.start")
    assert start["data"]["wake_word"] is None
    for page in make_opus(0.5, 500):
        await station.send_audio(3, page)
    kind, seq, chunk = await asyncio.wait_for(ha.audio.get(), 5)
    assert seq == start["data"]["turn_seq"] and chunk


async def test_announce_boomerang(station, ha, tts_file):
    reply = await ha.request("voice.announce", DEVICE_ID, {
        "media_url": tts_file, "text": "Внимание", "listen": False,
    })
    turn_id = reply["data"]["turn_id"]
    push = await station.expect_event("Push")
    directive = push.Event.Push.Directives[0]
    assert directive.Name == "@@mm_semantic_frame" and directive.Type == "server_action"
    payload = MessageToDict(directive.Payload)

    await station.send(station.text_input_server_action("ann-1", "@@mm_semantic_frame", payload))
    resp = await station.expect_event("AliceResponse")
    assert resp.Event.AliceResponse.VoiceResponse.OutputSpeech.Text == "Внимание"
    await station.expect_event("TtsSpeak")
    while True:
        msg, frame = await station.recv()
        if msg is not None:
            break
    await station.send(station.request_stat("stat-3", "ann-1"))
    finished = await ha.expect("voice.playback_finished")
    assert finished["data"]["turn_id"] == turn_id


async def test_media_play_resolves_redirect(station, ha, aiohttp_server):
    async def redirect(request):
        raise web.HTTPPermanentRedirect("/track.mp3")

    async def track(request):
        return web.Response(body=b"\xff\xfb" * 1000, content_type="audio/mpeg")

    app = web.Application()
    app.router.add_get("/r", redirect)
    app.router.add_get("/track.mp3", track)
    media = await aiohttp_server(app)

    reply = await ha.request("media.play", DEVICE_ID, {
        "url": str(media.make_url("/r")), "metadata": {"title": "Song", "artist": "Band"},
    })
    assert reply["ok"], reply
    push = await station.expect_event("Push")
    d = push.Event.Push.Directives[0]
    assert d.Name == "audio_play"
    payload = MessageToDict(d.Payload)
    assert payload["stream"]["url"].endswith("/track.mp3")
    assert payload["metadata"]["title"] == "Song"
    assert set(payload["callbacks"]) == {"on_started", "on_stopped", "on_finished", "on_failed"}
    stream_id = payload["stream"]["id"]

    while (await ha.expect("station.state"))["data"]["player"]["state"] != "buffering":
        pass

    await station.send(station.text_input_server_action("cb-1", "qa_media_started", {"stream_id": stream_id}))
    await station.expect_event("AliceResponse")
    media_event = None
    while media_event is None:
        event = await asyncio.wait_for(ha.events.get(), 5)
        if event["type"] == "media.event":
            media_event = event
    assert media_event["data"]["event"] == "started"

    assert (await ha.request("volume.set", DEVICE_ID, {"level": 0.34}))["ok"]
    push = await station.expect_event("Push")
    vol = push.Event.Push.Directives[0]
    assert vol.Name == "sound_set_level" and MessageToDict(vol.Payload) == {"new_level": 3.0}


async def test_wake_word_switch(adapter, station, ha):
    server, http = adapter
    reply = await ha.request("config.wake_word", DEVICE_ID, {"wake_word": "yandex"})
    assert reply["ok"] and reply["data"]["active_wake_word"] == "yandex"
    push = await station.expect_event("Push")
    assert push.Event.Push.Directives[0].Name == "push_update_config"
    async with http.get(server.make_url(f"/quasar/get_sync_info?device_id={DEVICE_ID}")) as r:
        assert (await r.json())["config"]["account_config"]["spotter"] == "yandex"
    bad = await ha.request("config.wake_word", DEVICE_ID, {"wake_word": "siri"})
    assert bad["error"]["code"] == "invalid"


async def test_native_timer(station, ha):
    assert (await ha.request("timer.set", DEVICE_ID, {"timer_id": "t1", "duration": 300, "title": "чай"}))["ok"]
    d = (await station.expect_event("Push")).Event.Push.Directives[0]
    assert d.Name == "timer_set_directive" and d.DirectiveType == aapi.TIMER_DIRECTIVE_TYPES["set"]
    payload = aapi._Alarm.TTimerSetDirective()
    payload.ParseFromString(d.PayloadRaw)
    assert (payload.Timer.TimerId, payload.Timer.Duration, payload.Timer.Title) == ("t1", 300, "чай")

    assert (await ha.request("timer.pause", DEVICE_ID, {"timer_id": "t1"}))["ok"]
    d = (await station.expect_event("Push")).Event.Push.Directives[0]
    assert d.Name == "timer_pause_directive" and d.DirectiveType == aapi.TIMER_DIRECTIVE_TYPES["pause"]
    assert (await ha.request("timer.cancel", DEVICE_ID, {}))["error"]["code"] == "invalid"


async def test_offline_station(ha):
    reply = await ha.request("media.pause", "NOPE", {})
    assert reply["error"]["code"] == "station_offline"


@pytest.fixture
async def aiohttp_server():
    servers: list[TestServer] = []

    async def factory(app: web.Application) -> TestServer:
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return server

    yield factory
    for server in servers:
        await server.close()


async def test_mic_preamp_off_sends_raw_audio_but_keeps_speech_detection(adapter, station, ha):
    server, _http = adapter
    from quasar_adapter.audio import pcm_level_db
    from quasar_adapter.server import HUB_KEY

    # The test tone is about -21 dBFS raw: only the boosted copy passes this threshold.
    server.app[HUB_KEY].config.speech_threshold_db = -10
    reply = await ha.request("config.mic_preamp", DEVICE_ID, {"enabled": False})
    assert reply["ok"] and reply["data"]["mic_preamp"] is False
    assert (await ha.request("config.mic_preamp", DEVICE_ID, {"enabled": "no"}))["error"]["code"] == "invalid"

    await station.send(station.voice_input("voice-raw", 5, spotter=False))
    await station.expect_event("InputStartAck")
    start = await ha.expect("voice.start")
    for page in make_opus(1.5, 500):
        await station.send_audio(5, page)
    pcm = bytearray()
    while len(pcm) < 16000 * 2:
        pcm += (await asyncio.wait_for(ha.audio.get(), 5))[2]
    assert -26 < pcm_level_db(bytes(pcm)) < -16, "raw station level expected, not +30 dB"
    while True:
        asr = await station.expect_event("AsrResult", skip_partials=False)
        assert not asr.Event.AsrResult.EndOfUtt, "the speech placeholder partial never came"
        if asr.Event.AsrResult.Recognition:
            assert asr.Event.AsrResult.Recognition[0].Normalized == "…"
            break
    assert start["data"]["turn_seq"]


async def test_trim_wake_word_switch(adapter, station, ha):
    server, _http = adapter
    from quasar_adapter.server import HUB_KEY

    reply = await ha.request("config.trim_wake_word", DEVICE_ID, {"enabled": False})
    assert reply["ok"] and reply["data"]["trim_wake_word"] is False
    # With trimming off the command audio starts right at SpotterEnd: a leading tail + pause is not cut.
    await station.send(station.voice_input("voice-notrim", 7))
    await station.expect_event("InputStartAck")
    start = await ha.expect("voice.start")
    for page in make_opus(1.0, 300):
        await station.send_audio(7, page)
    await station.send(station.stream_control(7, "SpotterEnd"))
    for page in make_opus(0.5, 600):
        await station.send_audio(7, page)
    pcm = bytearray()
    while len(pcm) < 16000 * 2 * 0.45:
        kind, seq, chunk = await asyncio.wait_for(ha.audio.get(), 5)
        assert seq == start["data"]["turn_seq"]
        pcm += chunk
    assert server.app[HUB_KEY].sessions[DEVICE_ID]._voice.tail_trimmer is None
    assert (await ha.request("config.trim_wake_word", DEVICE_ID, {"enabled": True}))["data"]["trim_wake_word"]
