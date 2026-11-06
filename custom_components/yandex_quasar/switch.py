"""Per-station audio switches stored in the adapter: microphone preamp and wake-word tail trimming."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YandexQuasarConfigEntry
from .client import AdapterClient, AdapterError
from .entity import YandexQuasarEntity, setup_station_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StationSwitchDescription(SwitchEntityDescription):
    command: str  # adapter command taking {"enabled": bool}; the flag of the same name is in StationInfo


SWITCHES = (
    # On: the adapter boosts the very quiet station audio (app option mic_gain_db). Off: raw audio goes to Assist.
    StationSwitchDescription(key="mic_preamp", translation_key="mic_preamp", command="config.mic_preamp",
                             entity_category=EntityCategory.CONFIG),
    # On: the end of "Алиса" is cut from the start of the command when a pause follows it.
    StationSwitchDescription(key="trim_wake_word", translation_key="trim_wake_word", command="config.trim_wake_word",
                             entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: YandexQuasarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_station_entities(entry, async_add_entities, lambda client, device_id: [
        YandexQuasarStationSwitch(client, device_id, description) for description in SWITCHES
    ])


class YandexQuasarStationSwitch(YandexQuasarEntity, SwitchEntity):
    entity_description: StationSwitchDescription

    def __init__(self, client: AdapterClient, device_id: str, description: StationSwitchDescription) -> None:
        super().__init__(client, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return bool(self.station.get(self.entity_description.key, True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        try:
            info = await self._client.request(self.entity_description.command, self._device_id, {"enabled": enabled})
        except AdapterError as err:
            raise HomeAssistantError(f"Could not change {self.entity_description.key}: {err}") from err
        self._client.stations[self._device_id] = {**self.station, **info}
        self.async_write_ha_state()
