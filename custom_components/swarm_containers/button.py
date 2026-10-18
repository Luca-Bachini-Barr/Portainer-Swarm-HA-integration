"""Button to run the image update check now.

It only asks Portainer (read-only GET requests) whether newer images exist;
it never pulls images or changes services.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SwarmContainersConfigEntry
from .coordinator import SwarmContainersCoordinator
from .entity import SWARM, SwarmEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwarmContainersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the check-for-updates button on the swarm device."""
    async_add_entities([CheckUpdatesButton(entry.runtime_data)])


class CheckUpdatesButton(SwarmEntity, ButtonEntity):
    """Ask Portainer to compare every running image with its registry now."""

    _attr_translation_key = "check_updates"
    _attr_icon = "mdi:package-variant-closed-check"

    def __init__(self, coordinator: SwarmContainersCoordinator) -> None:
        super().__init__(coordinator, SWARM, SWARM, "check_updates")

    async def async_press(self) -> None:
        await self.coordinator.async_check_images_now()
