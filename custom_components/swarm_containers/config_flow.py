"""Config flow for Swarm Containers."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import SwarmContainersConfigEntry
from .client import (
    PortainerAuthError,
    PortainerClient,
    PortainerConnectionError,
    PortainerError,
)
from .const import (
    CONF_API_TOKEN,
    CONF_ENDPOINT_ID,
    CONF_IMAGE_CHECK,
    CONF_IMAGE_CHECK_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_STANDALONE,
    CONF_VERIFY_SSL,
    DEFAULT_IMAGE_CHECK_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_IMAGE_CHECK_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_IMAGE_CHECK_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_INTERVAL = NumberSelector(
    NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL,
        max=MAX_SCAN_INTERVAL,
        step=1,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)

_IMAGE_INTERVAL = NumberSelector(
    NumberSelectorConfig(
        min=MIN_IMAGE_CHECK_INTERVAL,
        max=MAX_IMAGE_CHECK_INTERVAL,
        step=60,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)


class SwarmContainersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._endpoints: list[dict[str, Any]] = []
        self._instance_id: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SwarmContainersConfigEntry) -> OptionsFlow:
        return SwarmContainersOptionsFlow()

    async def _validate(self, user_input: Mapping[str, Any]) -> dict[str, str]:
        """Check the URL and token, and load the environments. Returns form errors."""
        session = async_get_clientsession(self.hass, verify_ssl=user_input[CONF_VERIFY_SSL])
        client = PortainerClient(session, user_input["url"], user_input[CONF_API_TOKEN])
        try:
            status = await client.get_status()
            self._endpoints = await client.get_endpoints()
        except PortainerAuthError:
            return {"base": "invalid_auth"}
        except PortainerConnectionError:
            return {"base": "cannot_connect"}
        except PortainerError:
            return {"base": "unknown"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Portainer")
            return {"base": "unknown"}
        self._instance_id = str(status.get("InstanceID") or user_input["url"])
        if not self._endpoints:
            return {"base": "no_endpoints"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._validate(user_input)
            if not errors:
                self._data = dict(user_input)
                if len(self._endpoints) == 1:
                    return await self._create(str(self._endpoints[0]["Id"]))
                return await self.async_step_endpoint()
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("url", default=defaults.get("url", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(CONF_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(
                    CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
                ): bool,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _INTERVAL,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_endpoint(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create(user_input[CONF_ENDPOINT_ID])
        options = [{"value": str(e["Id"]), "label": str(e.get("Name", e["Id"]))} for e in self._endpoints]
        schema = vol.Schema(
            {
                vol.Required(CONF_ENDPOINT_ID): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="endpoint", data_schema=schema)

    async def _create(self, endpoint_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(f"{self._instance_id}#{endpoint_id}")
        self._abort_if_unique_id_configured()
        name = next(
            (str(e.get("Name")) for e in self._endpoints if str(e["Id"]) == endpoint_id),
            endpoint_id,
        )
        data = {
            "url": self._data["url"],
            CONF_API_TOKEN: self._data[CONF_API_TOKEN],
            CONF_VERIFY_SSL: self._data[CONF_VERIFY_SSL],
            CONF_ENDPOINT_ID: endpoint_id,
        }
        return self.async_create_entry(
            title=f"Swarm containers ({name})",
            data=data,
            options={CONF_SCAN_INTERVAL: int(self._data[CONF_SCAN_INTERVAL])},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await self._validate(
                {
                    "url": entry.data["url"],
                    CONF_VERIFY_SSL: entry.data[CONF_VERIFY_SSL],
                    CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                }
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)


class SwarmContainersOptionsFlow(OptionsFlow):
    """Change polling, the image update check and which containers get devices."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_IMAGE_CHECK: bool(user_input[CONF_IMAGE_CHECK]),
                    CONF_IMAGE_CHECK_INTERVAL: int(user_input[CONF_IMAGE_CHECK_INTERVAL]),
                    CONF_STANDALONE: bool(user_input[CONF_STANDALONE]),
                }
            )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): _INTERVAL,
                vol.Required(CONF_IMAGE_CHECK, default=options.get(CONF_IMAGE_CHECK, True)): bool,
                vol.Required(
                    CONF_IMAGE_CHECK_INTERVAL,
                    default=options.get(CONF_IMAGE_CHECK_INTERVAL, DEFAULT_IMAGE_CHECK_INTERVAL),
                ): _IMAGE_INTERVAL,
                vol.Required(CONF_STANDALONE, default=options.get(CONF_STANDALONE, True)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
