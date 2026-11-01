"""Buttons: the image update check, and restart / redeploy when management is on.

The image check only asks Portainer (read-only) whether newer images exist. The
restart and redeploy buttons exist only with "Allow management actions" turned on.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SwarmContainersConfigEntry
from .coordinator import SwarmContainersCoordinator
from .entity import CONTAINER, SERVICE, STACK, SWARM, SwarmEntity, add_entities_dynamically
from .manage import Manager, management_enabled

MANAGEMENT_SUFFIXES = ("_restart", "_redeploy")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwarmContainersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the check-for-updates button, plus the management buttons if allowed."""
    coordinator = entry.runtime_data
    async_add_entities([CheckUpdatesButton(coordinator)])
    if not management_enabled(coordinator):
        # Management was turned off: drop the buttons it created.
        registry = er.async_get(hass)
        for item in er.async_entries_for_config_entry(registry, entry.entry_id):
            if item.domain == "button" and item.unique_id.endswith(MANAGEMENT_SUFFIXES):
                registry.async_remove(item.entity_id)
        return
    entry.async_on_unload(
        add_entities_dynamically(
            coordinator,
            async_add_entities,
            {
                STACK: lambda key: [
                    RestartButton(coordinator, STACK, key),
                    RedeployButton(coordinator, STACK, key),
                ],
                SERVICE: lambda key: [RestartButton(coordinator, SERVICE, key)],
                CONTAINER: lambda key: [RestartButton(coordinator, CONTAINER, key)],
            },
        )
    )


class CheckUpdatesButton(SwarmEntity, ButtonEntity):
    """Ask Portainer to compare every running image with its registry now."""

    _attr_translation_key = "check_updates"
    _attr_icon = "mdi:package-variant-closed-check"

    def __init__(self, coordinator: SwarmContainersCoordinator) -> None:
        super().__init__(coordinator, SWARM, SWARM, "check_updates")

    async def async_press(self) -> None:
        await self.coordinator.async_check_images_now()


class RestartButton(SwarmEntity, ButtonEntity):
    """Restart a stack, service (rolling force update) or container."""

    _attr_translation_key = "restart"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: SwarmContainersCoordinator, kind: str, key: str) -> None:
        super().__init__(coordinator, kind, key, "restart")

    async def async_press(self) -> None:
        try:
            await Manager(self.coordinator).restart(self._kind, self._key)
        finally:
            await self.coordinator.async_request_refresh()


class RedeployButton(SwarmEntity, ButtonEntity):
    """Deploy a Portainer stack again, pulling newer images."""

    _attr_translation_key = "redeploy"
    _attr_icon = "mdi:cloud-refresh"

    def __init__(self, coordinator: SwarmContainersCoordinator, kind: str, key: str) -> None:
        super().__init__(coordinator, kind, key, "redeploy")

    @property
    def available(self) -> bool:
        stack = self.obj or {}
        return super().available and bool(stack.get("portainer_id")) and not stack.get("git")

    async def async_press(self) -> None:
        try:
            await Manager(self.coordinator).redeploy(self._key, pull_image=True, prune=False)
        finally:
            await self.coordinator.async_request_refresh()
