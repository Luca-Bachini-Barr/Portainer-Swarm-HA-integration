"""Sensors: a container table and total memory for each swarm node."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SwarmContainersConfigEntry
from .const import DOMAIN
from .coordinator import SwarmContainersCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwarmContainersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for each node, and for nodes that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_nodes() -> None:
        new = [node for node in coordinator.data if node not in known]
        entities: list[SensorEntity] = []
        for node in new:
            known.add(node)
            entities.append(SwarmNodeContainers(coordinator, entry, node))
            entities.append(SwarmNodeMemory(coordinator, entry, node))
        if entities:
            async_add_entities(entities)

    _add_new_nodes()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_nodes))


class SwarmNodeEntity(CoordinatorEntity[SwarmContainersCoordinator]):
    """Base entity attached to a swarm node device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SwarmContainersCoordinator,
        entry: SwarmContainersConfigEntry,
        node: str,
    ) -> None:
        super().__init__(coordinator)
        self._node = node
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{node}")},
            name=node,
            manufacturer="Docker Swarm",
            model="Swarm node",
        )

    @property
    def _node_data(self) -> dict[str, Any] | None:
        return self.coordinator.data.get(self._node)

    @property
    def available(self) -> bool:
        return super().available and self._node_data is not None


class SwarmNodeContainers(SwarmNodeEntity, SensorEntity):
    """Number of running containers on a node, with the full table as an attribute."""

    _attr_translation_key = "containers"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:docker"
    # The table is large and changes often, so keep it out of the recorder database.
    _unrecorded_attributes = frozenset({"containers"})

    def __init__(
        self,
        coordinator: SwarmContainersCoordinator,
        entry: SwarmContainersConfigEntry,
        node: str,
    ) -> None:
        super().__init__(coordinator, entry, node)
        self._attr_unique_id = f"{entry.entry_id}_{node}_containers"

    @property
    def native_value(self) -> int | None:
        data = self._node_data
        return data["running"] if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self._node_data
        if data is None:
            return None
        return {
            "total": data["total"],
            "unhealthy": data["unhealthy"],
            "containers": data["containers"],
        }


class SwarmNodeMemory(SwarmNodeEntity, SensorEntity):
    """Memory used by all containers on a node."""

    _attr_translation_key = "container_memory"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.MEBIBYTES
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: SwarmContainersCoordinator,
        entry: SwarmContainersConfigEntry,
        node: str,
    ) -> None:
        super().__init__(coordinator, entry, node)
        self._attr_unique_id = f"{entry.entry_id}_{node}_container_memory"

    @property
    def native_value(self) -> float | None:
        data = self._node_data
        return data["memory_mib"] if data else None
