"""Config flow for Yandex Station (Quasar Adapter)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import AdapterAuthError, AdapterClient, AdapterConnectionError
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN
from .station_setup import CONF_METHOD, StationSetupSteps

STATION_ZEROCONF_TYPE = "_yandexio._tcp.local."


def _loaded_client(hass: HomeAssistant) -> AdapterClient | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.client
    return None


class YandexQuasarConfigFlow(StationSetupSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str | None = None
        self._setup_defaults: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return YandexQuasarOptionsFlow()

    def _setup_client(self) -> AdapterClient | None:
        return _loaded_client(self.hass)

    async def _validate(self, host: str, port: int, token: str) -> tuple[dict[str, Any] | None, str | None]:
        client = AdapterClient(self.hass, async_get_clientsession(self.hass), host, port, token)
        try:
            info = await client.connect()
        except AdapterAuthError:
            return None, "invalid_auth"
        except AdapterConnectionError:
            return None, "cannot_connect"
        finally:
            await client.close()
        return info, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            info, error = await self._validate(user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_TOKEN])
            if error:
                errors["base"] = error
            else:
                assert info is not None
                await self.async_set_unique_id(info["adapter_id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
                )
                return self.async_create_entry(title=info.get("name") or "Quasar Adapter", data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=self._host or ""): str,
            vol.Required(CONF_PORT, default=self._port): int,
            vol.Required(CONF_TOKEN): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        if discovery_info.type == STATION_ZEROCONF_TYPE:
            return await self._discovered_station(discovery_info)
        adapter_id = discovery_info.properties.get("id")
        if not adapter_id:
            return self.async_abort(reason="no_adapter_id")
        await self.async_set_unique_id(adapter_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port}
        )
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._name = discovery_info.properties.get("name") or "Quasar Adapter"
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._host is not None
        if user_input is not None:
            _info, error = await self._validate(self._host, self._port, user_input[CONF_TOKEN])
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=self._name or "Quasar Adapter",
                    data={CONF_HOST: self._host, CONF_PORT: self._port, CONF_TOKEN: user_input[CONF_TOKEN]},
                )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            description_placeholders={"name": self._name or "", "host": f"{self._host}:{self._port}"},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            _info, error = await self._validate(entry.data[CONF_HOST], entry.data[CONF_PORT], user_input[CONF_TOKEN])
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}), errors=errors
        )

    # ---- a Yandex Station found on the network (glagol mDNS) -------------------------------------------------

    async def _discovered_station(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        device_id = discovery_info.properties.get("deviceId")
        if not device_id:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(f"station-{device_id}")
        self._abort_if_unique_id_configured()
        client = self._setup_client()
        if client is None:
            return self.async_abort(reason="no_adapter")
        if device_id in client.stations:
            return self.async_abort(reason="already_configured")
        self._setup_defaults = {CONF_HOST: discovery_info.host, CONF_METHOD: "ssh"}
        platform = discovery_info.properties.get("platform") or "Yandex Station"
        self.context["title_placeholders"] = {"name": f"{platform} ({discovery_info.host})"}
        return await self.async_step_station()

    async def async_step_station(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._setup_form("station", user_input, "station.provision")


class YandexQuasarOptionsFlow(StationSetupSteps, OptionsFlow):
    """Adapter options: set up a station over SSH/ADB or return it to the Yandex cloud."""

    def __init__(self) -> None:
        self._setup_defaults: dict[str, Any] = {}

    def _setup_client(self) -> AdapterClient | None:
        runtime = getattr(self.config_entry, "runtime_data", None)
        return runtime.client if runtime is not None else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["station", "restore"])

    async def async_step_station(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._setup_form("station", user_input, "station.provision")

    async def async_step_restore(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._setup_form("restore", user_input, "station.restore")
