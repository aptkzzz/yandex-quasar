"""One-shot setup of the local test Home Assistant (ha-dev/docker-compose.yml).

Onboards a 'dev' user, sets internal_url, adds Wyoming Whisper/Piper, a Russian Assist pipeline and the
yandex_quasar integration. Credentials and a long-lived token are written to ha-dev/credentials.txt (gitignored).

Usage (from adapter/): uv run python ../ha-dev/setup_ha.py --lan-ip 192.168.1.132
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
from pathlib import Path

import aiohttp

HA = "http://localhost:8123"
CLIENT_ID = f"{HA}/"
HERE = Path(__file__).resolve().parent
ADAPTER_TOKEN = HERE.parent / "adapter" / "data" / "api_token"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan-ip", required=True)
    parser.add_argument("--adapter-port", type=int, default=8080)
    args = parser.parse_args()
    password = secrets.token_urlsafe(12)

    async with aiohttp.ClientSession() as http:
        async with http.post(f"{HA}/api/onboarding/users", json={
            "client_id": CLIENT_ID, "name": "Dev", "username": "dev", "password": password, "language": "ru",
        }) as resp:
            resp.raise_for_status()
            auth_code = (await resp.json())["auth_code"]
        async with http.post(f"{HA}/auth/token", data={
            "grant_type": "authorization_code", "code": auth_code, "client_id": CLIENT_ID,
        }) as resp:
            access = (await resp.json())["access_token"]
        headers = {"Authorization": f"Bearer {access}"}
        for step, body in (("core_config", {}), ("analytics", {}),
                           ("integration", {"client_id": CLIENT_ID, "redirect_uri": f"{HA}/?auth_callback=1"})):
            async with http.post(f"{HA}/api/onboarding/{step}", json=body, headers=headers) as resp:
                print("onboarding", step, resp.status)

        async def flow(handler: str, *steps: dict) -> dict:
            async with http.post(f"{HA}/api/config/config_entries/flow", json={"handler": handler},
                                 headers=headers) as resp:
                result = await resp.json()
            for data in steps:
                async with http.post(f"{HA}/api/config/config_entries/flow/{result['flow_id']}", json=data,
                                     headers=headers) as resp:
                    result = await resp.json()
            print("flow", handler, result.get("type"), result.get("title") or result.get("errors"))
            return result

        await flow("wyoming", {"host": "whisper", "port": 10300})
        await flow("wyoming", {"host": "piper", "port": 10200})
        await flow("yandex_quasar", {
            "host": args.lan_ip, "port": args.adapter_port, "token": ADAPTER_TOKEN.read_text().strip(),
        })

        async with http.ws_connect(f"{HA}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": access})
            await ws.receive_json()
            msg_id = 0

            async def call(payload: dict) -> dict:
                nonlocal msg_id
                msg_id += 1
                await ws.send_json({"id": msg_id, **payload})
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == msg_id:
                        if not reply.get("success"):
                            print("ws error", payload["type"], reply.get("error"))
                        return reply.get("result")

            await call({"type": "config/core/update", "internal_url": f"http://{args.lan_ip}:8123",
                        "language": "ru", "country": "ES", "time_zone": "Europe/Madrid"})
            await asyncio.sleep(2)
            states = await call({"type": "get_states"})
            stt = next(s["entity_id"] for s in states if s["entity_id"].startswith("stt."))
            tts = next(s["entity_id"] for s in states if s["entity_id"].startswith("tts."))
            pipeline = await call({
                "type": "assist_pipeline/pipeline/create", "name": "Русский (локальный)", "language": "ru",
                "conversation_engine": "conversation.home_assistant", "conversation_language": "ru",
                "stt_engine": stt, "stt_language": "ru", "tts_engine": tts, "tts_language": "ru_RU",
                "tts_voice": "ru_RU-irina-medium", "wake_word_entity": None, "wake_word_id": None,
            })
            await call({"type": "assist_pipeline/pipeline/set_preferred", "pipeline_id": pipeline["id"]})
            token = await call({"type": "auth/long_lived_access_token", "client_name": "dev-scripts",
                                "lifespan": 3650})

    (HERE / "credentials.txt").write_text(f"user: dev\npassword: {password}\ntoken: {token}\n")
    print(f"Pipeline: STT={stt} TTS={tts}. Credentials in {HERE / 'credentials.txt'}")


if __name__ == "__main__":
    asyncio.run(main())
