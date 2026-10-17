"""Swarm Containers: per-node container tables from Portainer."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SwarmContainersCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type SwarmContainersConfigEntry = ConfigEntry[SwarmContainersCoordinator]


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
