"""glagol client: volume pushed by the station reaches HA."""

from __future__ import annotations

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from quasar_adapter.glagol import GlagolClient
from quasar_adapter.server import HUB_KEY

from .conftest import DEVICE_ID


async def test_volume_from_glagol_reaches_ha(adapter, station, ha):
    server, _http = adapter
    hub = server.app[HUB_KEY]
    received: list[dict] = []

    async def glagol(request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received.append(await ws.receive_json())
        await ws.send_json({"state": {"volume": 0.7, "playing": False}, "sentTime": 1})
        await asyncio.sleep(1)
        return ws

    app = web.Application()
    app.router.add_get("/", glagol)
    fake = TestServer(app)
    await fake.start_server()
    client = GlagolClient(hub, hub.stations[DEVICE_ID], url=str(fake.make_url("/")).replace("http", "ws"))
    client.start()
    try:
        while True:
            event = await ha.expect("station.state")
            if event["data"]["volume"] == 0.7:
                break
        hello = received[0]
        assert hello["payload"] == {"command": "softwareVersion"}
        assert hello["conversationToken"].count(".") == 2
    finally:
        await client.stop()
        await fake.close()
