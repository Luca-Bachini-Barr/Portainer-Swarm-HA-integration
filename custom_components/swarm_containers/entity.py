"""Base entities and device info for Swarm Containers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MODEL_CONTAINER, MODEL_NODE, MODEL_SERVICE, MODEL_STACK, MODEL_SWARM
from .coordinator import (
    SwarmContainersCoordinator,
    container_identifier,
    node_identifier,
    service_identifier,
    stack_identifier,
    swarm_identifier,
)

# The kinds of object an entity can belong to, and where each lives in the snapshot.
SWARM = "swarm"
NODE = "node"
STACK = "stack"
SERVICE = "service"
CONTAINER = "container"


def lookup(data: dict[str, Any], kind: str, key: str) -> dict[str, Any] | None:
    """Return the snapshot object an entity reads from."""
    if kind == SWARM:
        return data
    if kind == NODE:
        return data["nodes"].get(key)
    if kind == STACK:
        return data["stacks"].get(key)
    if kind == SERVICE:
        return data["services"].get(key)
    return data["standalone"].get(key)


def keys_for(data: dict[str, Any], kind: str) -> Iterable[str]:
    """Return the keys of every object of a kind in the snapshot."""
    if kind == SWARM:
        return [SWARM]
    if kind == NODE:
        return data["nodes"].keys()
    if kind == STACK:
        return data["stacks"].keys()
    if kind == SERVICE:
        return data["services"].keys()
    return data["standalone"].keys()


def _parent(data: dict[str, Any], kind: str, key: str) -> tuple[str, str] | None:
    """Return the (kind, key) of the device an object's device hangs off."""
    if kind == SWARM:
        return None
    if kind == SERVICE:
        stack = (data["services"].get(key) or {}).get("stack")
        return (STACK, stack) if stack in data["stacks"] else (SWARM, SWARM)
    if kind == CONTAINER:
        node = (data["standalone"].get(key) or {}).get("node")
        return (NODE, node) if node in data["nodes"] else (SWARM, SWARM)
    return (SWARM, SWARM)


def _device_fields(coordinator: SwarmContainersCoordinator, kind: str, key: str) -> dict[str, Any]:
    """Return the device registry fields for an object, without its parent."""
    entry_id = coordinator.config_entry.entry_id
    data = coordinator.data
    base_url = str(coordinator.config_entry.data["url"]).rstrip("/")
    endpoint = coordinator.endpoint_id
    if kind == SWARM:
        return {
            "identifiers": {swarm_identifier(entry_id)},
            "name": "Docker Swarm",
            "manufacturer": "Docker Swarm",
            "model": MODEL_SWARM,
            "configuration_url": f"{base_url}/#!/{endpoint}/docker/swarm",
        }
    if kind == NODE:
        info = (data["nodes"].get(key) or {}).get("info") or {}
        return {
            "identifiers": {node_identifier(entry_id, key)},
            "name": key,
            "manufacturer": "Docker Swarm",
            "model": MODEL_NODE,
            "sw_version": info.get("engine_version"),
        }
    if kind == STACK:
        stack = data["stacks"].get(key) or {}
        url = f"{base_url}/#!/{endpoint}/docker/stacks/{key}"
        if stack.get("portainer_id"):
            url += f"?id={stack['portainer_id']}&type=1&regular=true"
        return {
            "identifiers": {stack_identifier(entry_id, key)},
            "name": f"Stack {key}",
            "manufacturer": "Docker Swarm",
            "model": MODEL_STACK,
            "configuration_url": url,
        }
    if kind == SERVICE:
        service = data["services"].get(key) or {}
        return {
            "identifiers": {service_identifier(entry_id, key)},
            "name": f"Service {key}",
            "manufacturer": "Docker Swarm",
            "model": MODEL_SERVICE,
            "sw_version": service.get("image"),
            "configuration_url": f"{base_url}/#!/{endpoint}/docker/services/{service.get('id', '')}",
        }
    container = data["standalone"].get(key) or {}
    return {
        "identifiers": {container_identifier(entry_id, key)},
        "name": key.replace("/", " "),
        "manufacturer": "Docker",
        "model": MODEL_CONTAINER,
        "sw_version": container.get("image"),
    }


@callback
def _ensure_device(coordinator: SwarmContainersCoordinator, kind: str, key: str) -> str:
    """Register an object's device (and its parents first) and return its id."""
    parent = _parent(coordinator.data, kind, key)
    via_device_id = _ensure_device(coordinator, *parent) if parent else None
    device = dr.async_get(coordinator.hass).async_get_or_create(
        config_entry_id=coordinator.config_entry.entry_id,
        via_device_id=via_device_id,
        **_device_fields(coordinator, kind, key),
    )
    return device.id


def device_info(coordinator: SwarmContainersCoordinator, kind: str, key: str) -> DeviceInfo:
    """Build the device for an object, linked to its parent by device id.

    Nodes, stacks and standalone containers hang off the swarm device (containers off
    their node), and services off their stack.
    """
    info = DeviceInfo(**_device_fields(coordinator, kind, key))
    if parent := _parent(coordinator.data, kind, key):
        info["via_device_id"] = _ensure_device(coordinator, *parent)
    return info


class SwarmEntity(CoordinatorEntity[SwarmContainersCoordinator]):
    """An entity that reads one object (swarm, node, stack, service or container)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SwarmContainersCoordinator, kind: str, key: str, suffix: str
    ) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._key = key
        entry_id = coordinator.config_entry.entry_id
        # Node sensors keep the unique ids of version 0.1 so existing entities survive.
        if kind == NODE:
            self._attr_unique_id = f"{entry_id}_{key}_{suffix}"
        elif kind == SWARM:
            self._attr_unique_id = f"{entry_id}_swarm_{suffix}"
        else:
            self._attr_unique_id = f"{entry_id}_{kind}_{key}_{suffix}"
        self._attr_device_info = device_info(coordinator, kind, key)

    @property
    def obj(self) -> dict[str, Any] | None:
        return lookup(self.coordinator.data, self._kind, self._key)

    @property
    def available(self) -> bool:
        return super().available and self.obj is not None


def add_entities_dynamically(
    coordinator: SwarmContainersCoordinator,
    add: Callable[[list[Entity]], None],
    factories: dict[str, Callable[[str], list[Entity]]],
) -> Callable[[], None]:
    """Create entities for every object now, and for new objects as they appear.

    Returns the listener remover, for ``entry.async_on_unload``.
    """
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        entities: list[Entity] = []
        for kind, factory in factories.items():
            present = set(keys_for(coordinator.data, kind))
            # Forget objects that went away, so they get entities again if they come back.
            known.difference_update({(k, key) for k, key in known if k == kind and key not in present})
            for key in sorted(present):
                if (kind, key) not in known:
                    known.add((kind, key))
                    entities.extend(factory(key))
        if entities:
            add(entities)

    _add_new()
    return coordinator.async_add_listener(_add_new)
