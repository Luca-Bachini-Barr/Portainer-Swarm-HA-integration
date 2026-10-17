"""Swarm Containers: a read-only view of a Docker Swarm through Portainer."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, FRONTEND_URL, VERSION
from .coordinator import SwarmContainersCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SwarmContainersConfigEntry = ConfigEntry[SwarmContainersCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the bundled dashboard cards and register them with the dashboards."""
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL,
                str(Path(__file__).parent / "frontend" / "swarm-cards.js"),
                cache_headers=False,
            )
        ]
    )
    await _async_register_cards(hass)
    return True


async def _async_register_cards(hass: HomeAssistant) -> None:
    """Add the cards as a dashboard resource, or update its version after an upgrade.

    Dashboards wait for their resources before drawing cards; a script added with
    add_extra_js_url can arrive too late and leave the cards as configuration errors.
    The version query makes browsers fetch the new file after an update.
    """
    url = f"{FRONTEND_URL}?v={VERSION}"
    resources = getattr(hass.data.get("lovelace"), "resources", None)
    if resources is None or not hasattr(resources, "async_create_item"):
        # Resources are managed in YAML, so they can't be edited; load on every page instead.
        add_extra_js_url(hass, url)
        return
    await resources.async_get_info()
    for item in resources.async_items():
        if str(item.get("url", "")).split("?", 1)[0] == FRONTEND_URL:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
            return
    await resources.async_create_item({"res_type": "module", "url": url})


async def async_setup_entry(hass: HomeAssistant, entry: SwarmContainersConfigEntry) -> bool:
    """Set up Swarm Containers from a config entry."""
    coordinator = SwarmContainersCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: SwarmContainersConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SwarmContainersConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
