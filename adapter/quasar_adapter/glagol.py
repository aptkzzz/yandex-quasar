"""Client for the station's local glagol WebSocket (wss://<station>:1961).

The station pushes its state there on every change (volume buttons included), which uniproxy never reports.
Tokens are signed by our own backend key, so the station's check_token call to the adapter accepts them.
Message format is the one used by the yandex_station integration (AlexxIT).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from .hub import Hub
    from .station import Station

_LOGGER = logging.getLogger(__name__)

GLAGOL_PORT = 1961
PING_INTERVAL = 30
RECONNECT_MIN = 2
RECONNECT_MAX = 60


class GlagolClient:
    def __init__(self, hub: Hub, station: Station, url: str | None = None) -> None:
        self.hub = hub
        self.station = station
        self._url = url
        self._task: asyncio.Task | None = None
        self.connected = False

    @property
    def url(self) -> str | None:
        if self._url:
            return self._url
        return f"wss://{self.station.state.ip}:{GLAGOL_PORT}" if self.station.state.ip else None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"glagol {self.station.device_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "conversationToken": self.hub.backend.mint_glagol_token(self.station.device_id, self.station.platform),
            "id": str(uuid.uuid4()),
            "payload": payload,
            "sentTime": int(time.time() * 1000),
        }

    async def _run(self) -> None:
        delay = RECONNECT_MIN
        while True:
            url = self.url
            if url is None:
                await asyncio.sleep(RECONNECT_MIN)
                continue
            try:
                assert self.hub.http is not None
                async with self.hub.http.ws_connect(url, ssl=False, heartbeat=55, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                    self.connected = True
                    delay = RECONNECT_MIN
                    _LOGGER.info("[%s] glagol connected (%s)", self.station.device_id, url)
                    await ws.send_json(self._message({"command": "softwareVersion"}))
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._on_message(msg.json())
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                break
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as err:
                _LOGGER.debug("[%s] glagol connection failed: %s", self.station.device_id, err)
            if self.connected:
                _LOGGER.info("[%s] glagol disconnected", self.station.device_id)
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send_json(self._message({"command": "ping"}))

    def _on_message(self, data: dict[str, Any]) -> None:
        state = data.get("state")
        if not isinstance(state, dict):
            return
        volume = state.get("volume")
        if isinstance(volume, (int, float)):
            volume = round(float(volume), 2)
            if volume != self.station.state.volume:
                self.station.state.volume = volume
                self.hub.emit_state(self.station)
