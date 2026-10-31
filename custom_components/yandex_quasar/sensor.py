"""Connection status sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YandexQuasarConfigEntry
from .client import AdapterClient
from .entity import YandexQuasarEntity, setup_station_entities

PARALLEL_UPDATES = 0

STATUS_ONLINE = "online"
STATUS_STATION_OFFLINE = "station_offline"
STATUS_ADAPTER_OFFLINE = "adapter_offline"


async def async_setup_entry(
    hass: HomeAssistant, entry: YandexQuasarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_station_entities(entry, async_add_entities, lambda client, device_id: [
        YandexQuasarStatusSensor(client, device_id)
    ])


class YandexQuasarStatusSensor(YandexQuasarEntity, SensorEntity):
    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_ONLINE, STATUS_STATION_OFFLINE, STATUS_ADAPTER_OFFLINE]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client: AdapterClient, device_id: str) -> None:
        super().__init__(client, device_id, "status")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not self._client.connected:
            return STATUS_ADAPTER_OFFLINE
        if self.station_state.get("connection") == "online":
            return STATUS_ONLINE
        return STATUS_STATION_OFFLINE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ip": self.station_state.get("ip"),
            "firmware": self.station.get("firmware"),
            "protocol": self.station.get("protocol"),
            "alice_state": self.station_state.get("alice_state"),
            "adapter_version": self._client.version,
        }
