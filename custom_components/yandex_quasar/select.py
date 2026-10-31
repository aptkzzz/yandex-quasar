"""Assist pipeline and VAD sensitivity selects for each station."""

from __future__ import annotations

from homeassistant.components.assist_pipeline import AssistPipelineSelect, VadSensitivitySelect
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YandexQuasarConfigEntry
from .client import AdapterClient
from .const import DOMAIN
from .entity import YandexQuasarEntity, setup_station_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: YandexQuasarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_station_entities(entry, async_add_entities, lambda client, device_id: [
        YandexQuasarPipelineSelect(hass, client, device_id),
        YandexQuasarVadSensitivitySelect(hass, client, device_id),
    ])


class YandexQuasarPipelineSelect(YandexQuasarEntity, AssistPipelineSelect):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, client: AdapterClient, device_id: str) -> None:
        YandexQuasarEntity.__init__(self, client, device_id, "pipeline")
        AssistPipelineSelect.__init__(self, hass, DOMAIN, device_id)

    @property
    def available(self) -> bool:
        return True  # configuration is meaningful even while the station is offline


class YandexQuasarVadSensitivitySelect(YandexQuasarEntity, VadSensitivitySelect):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, client: AdapterClient, device_id: str) -> None:
        YandexQuasarEntity.__init__(self, client, device_id, "vad_sensitivity")
        VadSensitivitySelect.__init__(self, hass, device_id)

    @property
    def available(self) -> bool:
        return True
