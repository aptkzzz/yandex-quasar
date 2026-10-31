"""Shared entity helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YandexQuasarConfigEntry
from .client import AdapterClient
from .const import DOMAIN, MANUFACTURER

PLATFORM_MODELS = {
    "cucumber": "Станция Миди",
    "yandexstation_2": "Станция 2",
    "yandexmini_2": "Станция Мини 2",
    "yandexmicro": "Станция Лайт",
    "chiron": "Станция Макс",
    "bergamot": "Станция Мини 3",
    "plum": "Станция Лайт 2",
    "orion": "Станция 3",
}


def station_device_info(client: AdapterClient, device_id: str) -> DeviceInfo:
    info = client.stations.get(device_id, {})
    platform = info.get("platform", "")
    return DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=info.get("name") or f"Яндекс Станция {device_id[-4:]}",
        manufacturer=MANUFACTURER,
        model=PLATFORM_MODELS.get(platform, platform or None),
        model_id=platform or None,
        serial_number=device_id,
        sw_version=info.get("firmware") or None,
    )


class YandexQuasarEntity(Entity):
    """Entity bound to one station; pushes state from the adapter connection."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client: AdapterClient, device_id: str, key: str) -> None:
        self._client = client
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}-{key}"
        self._attr_device_info = station_device_info(client, device_id)

    @property
    def station(self) -> dict[str, Any]:
        return self._client.stations.get(self._device_id, {})

    @property
    def station_state(self) -> dict[str, Any]:
        return self.station.get("state") or {}

    @property
    def available(self) -> bool:
        return self._client.connected and self.station_state.get("connection") == "online"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe_connection(self._handle_update))
        self.async_on_remove(self._client.subscribe_state(self._device_id, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


def setup_station_entities(
    entry: YandexQuasarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[AdapterClient, str], Iterable[Entity]],
) -> None:
    """Add entities for known stations and for stations the adapter reports later."""
    client = entry.runtime_data.client
    added: set[str] = set()

    @callback
    def add(device_id: str) -> None:
        if device_id in added:
            return
        added.add(device_id)
        async_add_entities(list(factory(client, device_id)))

    for device_id in list(client.stations):
        add(device_id)
    entry.async_on_unload(client.subscribe_added(add))
