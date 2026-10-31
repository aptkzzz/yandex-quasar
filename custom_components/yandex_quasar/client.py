"""WebSocket client for the Quasar Adapter HA API (docs/adapter-ha-api.md)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
import logging
import struct
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

API_VERSION = 1
REQUEST_TIMEOUT = 10
RECONNECT_MIN = 1
RECONNECT_MAX = 30

StateListener = Callable[[], None]
EventListener = Callable[[str, dict[str, Any]], None]
AudioHandler = Callable[[bytes], None]


class AdapterError(Exception):
    """Base error."""


class AdapterConnectionError(AdapterError):
    """Adapter is unreachable or the connection dropped."""


class AdapterAuthError(AdapterError):
    """Token rejected."""


class AdapterCommandError(AdapterError):
    """Adapter answered a request with ok=false."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class AdapterClient:
    """Keeps a WebSocket to the adapter open, mirrors station state and routes events."""

    def __init__(
        self, hass: HomeAssistant, session: aiohttp.ClientSession, host: str, port: int, token: str
    ) -> None:
        self.hass = hass
        self._session = session
        self.url = f"ws://{host}:{port}/ha"
        self._token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._loop_task: asyncio.Task | None = None
        self._closing = False
        self.connected = False
        self.adapter_id: str | None = None
        self.adapter_name: str | None = None
        self.version: str | None = None
        self.stations: dict[str, dict[str, Any]] = {}
        self._connection_listeners: list[StateListener] = []
        self._added_listeners: list[Callable[[str], None]] = []
        self._state_listeners: dict[str, list[StateListener]] = {}
        self._event_listeners: dict[str, list[EventListener]] = {}
        self._audio_handlers: dict[int, AudioHandler] = {}
        self._global_listeners: list[Callable[[str, str, dict[str, Any]], None]] = []

    # ---- lifecycle ------------------------------------------------------------------------------------------

    async def connect(self) -> dict[str, Any]:
        """Open the socket and perform hello; raises AdapterConnectionError / AdapterAuthError."""
        try:
            ws = await self._session.ws_connect(self.url, heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_close=10))
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise AdapterConnectionError(str(err)) from err
        try:
            await ws.send_json({"type": "hello", "id": 0, "data": {"token": self._token, "api": API_VERSION}})
            reply = await ws.receive_json(timeout=REQUEST_TIMEOUT)
        except (aiohttp.ClientError, TimeoutError, TypeError, ValueError) as err:
            await ws.close()
            raise AdapterConnectionError(str(err)) from err
        if not reply.get("ok"):
            await ws.close()
            if (reply.get("error") or {}).get("code") == "invalid_auth":
                raise AdapterAuthError("invalid token")
            raise AdapterConnectionError(str(reply.get("error")))
        data = reply["data"]
        self._ws = ws
        self.adapter_id = data["adapter_id"]
        self.adapter_name = data.get("name")
        self.version = data.get("version")
        for info in data.get("stations", []):
            self.stations[info["device_id"]] = info
        self._set_connected(True)
        return data

    def start(self, create_task: Callable[..., asyncio.Task]) -> None:
        """Start the reader/reconnect loop (pass entry.async_create_background_task bound to hass)."""
        self._loop_task = create_task(self._run(), "yandex_quasar adapter connection")

    async def close(self) -> None:
        self._closing = True
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def _run(self) -> None:
        delay = RECONNECT_MIN
        while not self._closing:
            if self._ws is None or self._ws.closed:
                try:
                    await self.connect()
                    _LOGGER.info("Reconnected to Quasar Adapter at %s", self.url)
                    delay = RECONNECT_MIN
                except AdapterError as err:
                    _LOGGER.debug("Adapter connect failed: %s", err)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX)
                    continue
            await self._read_loop()
            self._set_connected(False)
            if not self._closing:
                _LOGGER.warning("Lost connection to Quasar Adapter at %s", self.url)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    self._on_json(json.loads(msg.data))
                except Exception:
                    _LOGGER.exception("Failed to handle adapter message")
            elif msg.type == aiohttp.WSMsgType.BINARY:
                self._on_binary(msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AdapterConnectionError("connection closed"))
        self._pending.clear()
        self._audio_handlers.clear()

    @callback
    def _set_connected(self, value: bool) -> None:
        if self.connected == value:
            return
        self.connected = value
        for listener in list(self._connection_listeners):
            listener()

    # ---- incoming -------------------------------------------------------------------------------------------

    @callback
    def _on_json(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "result":
            fut = self._pending.pop(msg.get("id"), None)
            if fut and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("data") or {})
                else:
                    err = msg.get("error") or {}
                    fut.set_exception(AdapterCommandError(err.get("code", "unknown"), err.get("message", "")))
            return
        station = msg.get("station")
        data = msg.get("data") or {}
        if kind == "station.added":
            self.stations[station] = data
            for listener in list(self._added_listeners):
                listener(station)
            return
        if kind == "station.state":
            info = self.stations.setdefault(station, {"device_id": station})
            info["state"] = data
            for listener in list(self._state_listeners.get(station, [])):
                listener()
            return
        for global_listener in list(self._global_listeners):
            global_listener(station, kind, data)
        for listener in list(self._event_listeners.get(station, [])):
            listener(kind, data)

    @callback
    def _on_binary(self, data: bytes) -> None:
        if len(data) < 5:
            return
        _kind, seq = struct.unpack(">BI", data[:5])
        handler = self._audio_handlers.get(seq)
        if handler is not None:
            handler(data[5:])

    # ---- outgoing -------------------------------------------------------------------------------------------

    async def request(self, type_: str, station: str | None = None, data: dict | None = None) -> dict:
        if self._ws is None or self._ws.closed:
            raise AdapterConnectionError("not connected")
        self._msg_id += 1
        msg_id = self._msg_id
        fut: asyncio.Future[dict] = self.hass.loop.create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send_json({"type": type_, "id": msg_id, "station": station, "data": data or {}})
            async with asyncio.timeout(REQUEST_TIMEOUT):
                return await fut
        except (aiohttp.ClientError, ConnectionError) as err:
            raise AdapterConnectionError(str(err)) from err
        except TimeoutError as err:
            raise AdapterConnectionError(f"{type_} timed out") from err
        finally:
            self._pending.pop(msg_id, None)

    # ---- subscriptions --------------------------------------------------------------------------------------

    @callback
    def subscribe_connection(self, listener: StateListener) -> Callable[[], None]:
        self._connection_listeners.append(listener)
        return lambda: self._connection_listeners.remove(listener)

    @callback
    def subscribe_added(self, listener: Callable[[str], None]) -> Callable[[], None]:
        self._added_listeners.append(listener)
        return lambda: self._added_listeners.remove(listener)

    @callback
    def subscribe_all_events(self, listener: Callable[[str, str, dict[str, Any]], None]) -> Callable[[], None]:
        """Events of every station: listener(station_id, type, data)."""
        self._global_listeners.append(listener)
        return lambda: self._global_listeners.remove(listener)

    @callback
    def subscribe_state(self, station: str, listener: StateListener) -> Callable[[], None]:
        self._state_listeners.setdefault(station, []).append(listener)
        return lambda: self._state_listeners[station].remove(listener)

    @callback
    def subscribe_events(self, station: str, listener: EventListener) -> Callable[[], None]:
        self._event_listeners.setdefault(station, []).append(listener)
        return lambda: self._event_listeners[station].remove(listener)

    @callback
    def set_audio_handler(self, turn_seq: int, handler: AudioHandler | None) -> None:
        if handler is None:
            self._audio_handlers.pop(turn_seq, None)
        else:
            self._audio_handlers[turn_seq] = handler
