"""Minimal stand-in for the HA integration, for testing the adapter on a real station.

Connects to the adapter HA API, records each voice turn to captures/echo/<turn>.wav, waits for ~1 s of silence
(simple energy VAD) and answers with macOS `say`: "Записала N секунд".

Usage: uv run python tools/echo_ha.py [--url ws://127.0.0.1:8080/ha] [--token TOKEN]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

import aiohttp
import numpy as np

OUT_DIR = Path(__file__).resolve().parents[2] / "captures" / "echo"
SILENCE_DB = -45.0
SILENCE_SECONDS = 1.0
MIN_SPEECH_SECONDS = 0.3


class Turn:
    def __init__(self, turn_id: str, station: str) -> None:
        self.turn_id = turn_id
        self.station = station
        self.pcm = bytearray()
        self.speech = 0.0
        self.silence = 0.0
        self.done = False


async def main() -> None:
    while True:
        try:
            await run_once()
        except (aiohttp.ClientError, ConnectionError) as err:
            print("connection lost:", err)
        await asyncio.sleep(3)


async def run_once() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8080/ha")
    parser.add_argument("--token", default=(Path(__file__).resolve().parents[1] / "data" / "api_token"))
    args = parser.parse_args()
    token = Path(args.token).read_text().strip() if Path(str(args.token)).exists() else str(args.token)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    turns: dict[int, Turn] = {}
    by_id: dict[str, Turn] = {}
    msg_id = 0

    async with aiohttp.ClientSession() as http, http.ws_connect(args.url) as ws:
        await ws.send_json({"type": "hello", "id": 0, "data": {"token": token, "api": 1}})
        hello = await ws.receive_json()
        print("hello:", json.dumps(hello, ensure_ascii=False)[:300])

        async def request(type_: str, station: str, data: dict) -> None:
            nonlocal msg_id
            msg_id += 1
            await ws.send_json({"type": type_, "id": msg_id, "station": station, "data": data})

        async def finish(turn: Turn) -> None:
            turn.done = True
            seconds = len(turn.pcm) / 32000
            wav_path = OUT_DIR / f"{turn.turn_id}.wav"
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(turn.pcm))
            print(f"turn {turn.turn_id}: {seconds:.1f}s -> {wav_path}")
            await request("voice.stt_end", turn.station, {"turn_id": turn.turn_id, "text": "эхо"})
            text = f"Записала {seconds:.1f} секунды".replace(".", ",")
            tmp = Path(tempfile.mkdtemp()) / "answer.aiff"
            subprocess.run(["say", "-v", "Milena", "-o", str(tmp), text], check=True)
            await request("voice.respond", turn.station, {
                "turn_id": turn.turn_id, "text": text, "tts_url": str(tmp), "listen": False,
            })

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                event = json.loads(msg.data)
                kind = event["type"]
                if kind == "voice.start":
                    turn = Turn(event["data"]["turn_id"], event["station"])
                    turns[event["data"]["turn_seq"]] = turn
                    by_id[turn.turn_id] = turn
                    print("voice.start", event["data"])
                elif kind in ("voice.stop", "voice.playback_finished", "media.event", "result"):
                    print(kind, json.dumps(event.get("data") or event, ensure_ascii=False)[:200])
                    if kind == "voice.stop" and (t := by_id.get(event["data"]["turn_id"])) and not t.done:
                        if event["data"]["reason"] == "end":
                            await finish(t)
                        else:
                            t.done = True
            elif msg.type == aiohttp.WSMsgType.BINARY:
                _, seq = struct.unpack(">BI", msg.data[:5])
                turn = turns.get(seq)
                if turn is None or turn.done:
                    continue
                chunk = msg.data[5:]
                turn.pcm += chunk
                samples = np.frombuffer(chunk, "<i2").astype(np.float32)
                rms = np.sqrt(np.mean(samples ** 2)) if len(samples) else 0.0
                level = 20 * np.log10(rms / 32768 + 1e-9)
                duration = len(samples) / 16000
                if level > SILENCE_DB:
                    turn.speech += duration
                    turn.silence = 0.0
                else:
                    turn.silence += duration
                if turn.speech >= MIN_SPEECH_SECONDS and turn.silence >= SILENCE_SECONDS:
                    await finish(turn)


if __name__ == "__main__":
    asyncio.run(main())
