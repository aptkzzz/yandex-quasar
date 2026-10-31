"""Config flow for Yandex Station (Quasar Adapter)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import AdapterAuthError, AdapterClient, AdapterConnectionError
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN


class YandexQuasarConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str | None = None

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
