"""Yandex Station via Quasar Adapter."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import AdapterAuthError, AdapterClient, AdapterConnectionError
from .const import CONF_TOKEN

PLATFORMS = [Platform.ASSIST_SATELLITE, Platform.MEDIA_PLAYER, Platform.SELECT, Platform.SENSOR]


@dataclass
class YandexQuasarData:
    client: AdapterClient


type YandexQuasarConfigEntry = ConfigEntry[YandexQuasarData]


async def async_setup_entry(hass: HomeAssistant, entry: YandexQuasarConfigEntry) -> bool:
    client = AdapterClient(
        hass, async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN]
    )
    try:
        await client.connect()
    except AdapterAuthError as err:
        raise ConfigEntryAuthFailed("Adapter rejected the token") from err
    except AdapterConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot connect to Quasar Adapter: {err}") from err

    entry.runtime_data = YandexQuasarData(client)
    client.start(lambda coro, name: entry.async_create_background_task(hass, coro, name))
    entry.async_on_unload(client.close)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YandexQuasarConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
