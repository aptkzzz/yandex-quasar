"""WebSocket API for the Home Assistant integration (see «Протокол Adapter ↔ HA» in the project Notion)."""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from . import __version__
from .session import CommandError

if TYPE_CHECKING:
    from .hub import Hub

_LOGGER = logging.getLogger(__name__)

API_VERSION = 1
HELLO_TIMEOUT = 10
SEND_QUEUE_LIMIT = 2000


class HAClient:
    """One connected HA instance. Outgoing frames go through a queue so emitters never block."""

    def __init__(self, ws: web.WebSocketResponse, remote: str | None) -> None:
        self.ws = ws
        self.remote = remote
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(SEND_QUEUE_LIMIT)
        self._writer: asyncio.Task | None = None

    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop())

    async def stop(self) -> None:
        if self._writer:
            self._writer.cancel()

    def send_json_nowait(self, message: dict) -> None:
        self._put(("json", message))

    def send_bytes_nowait(self, data: bytes) -> None:
        self._put(("bytes", data))

    def _put(self, item: tuple[str, Any]) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            _LOGGER.warning("HA client %s is too slow, dropping connection", self.remote)
            asyncio.get_running_loop().create_task(self.ws.close())

    async def _write_loop(self) -> None:
        while True:
            kind, payload = await self._queue.get()
            if self.ws.closed:
                return
            if kind == "json":
                await self.ws.send_json(payload)
            else:
                await self.ws.send_bytes(payload)


class HAApi:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def handle(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        client = HAClient(ws, request.remote)
        try:
            if not await self._hello(ws):
                return ws
            client.start()
            self.hub.ha_clients.add(client)
            _LOGGER.info("Home Assistant connected from %s", request.remote)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    asyncio.create_task(self._dispatch(client, msg.json()))
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            self.hub.ha_clients.discard(client)
            await client.stop()
            _LOGGER.info("Home Assistant %s disconnected", request.remote)
        return ws

    async def _hello(self, ws: web.WebSocketResponse) -> bool:
        try:
            msg = await ws.receive_json(timeout=HELLO_TIMEOUT)
        except (TimeoutError, ValueError, TypeError):
            await ws.close(code=4000, message=b"hello expected")
            return False
        data = msg.get("data") or {}
        if msg.get("type") != "hello" or not hmac.compare_digest(
            str(data.get("token", "")), self.hub.config.api_token
        ):
            await ws.send_json({"type": "result", "id": msg.get("id"), "ok": False,
                                "error": {"code": "invalid_auth", "message": "Invalid token"}})
            await ws.close(code=4001, message=b"invalid auth")
            return False
        await ws.send_json({"type": "result", "id": msg.get("id"), "ok": True, "data": {
            "adapter_id": self.hub.adapter_id,
            "name": self.hub.config.adapter_name,
            "version": __version__,
            "api": API_VERSION,
            "stations": [s.info() for s in self.hub.stations.values()],
        }})
        return True

    async def _dispatch(self, client: HAClient, msg: dict) -> None:
        msg_id = msg.get("id")
        type_ = msg.get("type", "")
        try:
            result = await self._execute(type_, msg.get("station"), msg.get("data") or {})
            reply = {"type": "result", "id": msg_id, "ok": True, "data": result}
        except CommandError as err:
            reply = {"type": "result", "id": msg_id, "ok": False,
                     "error": {"code": err.code, "message": str(err)}}
        except (KeyError, ValueError, TypeError) as err:
            reply = {"type": "result", "id": msg_id, "ok": False,
                     "error": {"code": "invalid", "message": repr(err)}}
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Command %s failed", type_)
            reply = {"type": "result", "id": msg_id, "ok": False,
                     "error": {"code": "internal", "message": str(err)}}
        if msg_id is not None:
            client.send_json_nowait(reply)

    async def _execute(self, type_: str, station_id: str | None, data: dict) -> dict:
        if type_ == "stations.list":
            return {"stations": [s.info() for s in self.hub.stations.values()]}
        if not station_id:
            raise CommandError("invalid", "station is required")
        session = self.hub.sessions.get(station_id)
        if session is None:
            if station_id in self.hub.stations and type_ == "station.get":
                return self.hub.stations[station_id].info()
            raise CommandError("station_offline")
        return await session.command(type_, data)
