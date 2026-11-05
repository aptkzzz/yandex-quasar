"""Per-station switches: the adapter's microphone preamp."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YandexQuasarConfigEntry
from .client import AdapterClient, AdapterError
from .entity import YandexQuasarEntity, setup_station_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: YandexQuasarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_station_entities(entry, async_add_entities, lambda client, device_id: [
        YandexQuasarMicPreampSwitch(client, device_id),
    ])


class YandexQuasarMicPreampSwitch(YandexQuasarEntity, SwitchEntity):
    """On: the adapter boosts the very quiet station audio (app option mic_gain_db). Off: raw audio goes to Assist,
    for pipelines that do their own gain/processing."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "mic_preamp"

    def __init__(self, client: AdapterClient, device_id: str) -> None:
        super().__init__(client, device_id, "mic_preamp")

    @property
    def is_on(self) -> bool:
        return bool(self.station.get("mic_preamp", True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        try:
            info = await self._client.request("config.mic_preamp", self._device_id, {"enabled": enabled})
        except AdapterError as err:
            raise HomeAssistantError(f"Could not change the microphone preamp: {err}") from err
        self._client.stations[self._device_id] = {**self.station, **info}
        self.async_write_ha_state()
