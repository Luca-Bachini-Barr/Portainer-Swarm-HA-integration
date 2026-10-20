"""Binary sensors: swarm problems, node connectivity and image updates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SwarmContainersConfigEntry
from .coordinator import SwarmContainersCoordinator
from .entity import CONTAINER, NODE, SERVICE, STACK, SWARM, SwarmEntity, add_entities_dynamically
from .parse import OUTDATED


@dataclass(frozen=True, kw_only=True)
class SwarmBinaryDescription(BinarySensorEntityDescription):
    """Describes a binary sensor and how to read it from its snapshot object."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def swarm_problems(data: dict[str, Any]) -> list[str]:
    """Return a readable reason for each thing wrong in the swarm."""
    reasons = [
        f"Node {name} is {t['info'].get('state')}"
        for name, t in sorted(data["nodes"].items())
        if t["info"].get("role") and t["info"].get("state") != "ready"
    ]
    reasons += [
        f"Service {name} is {data['services'][name]['state'].replace('_', ' ')}"
        for name in data["problem_services"]
    ]
    if data["unhealthy"]:
        reasons.append(f"{data['unhealthy']} unhealthy container(s)")
    return reasons


def _update_value(obj: dict[str, Any]) -> bool | None:
    status = obj.get("update")
    if status is None or status in ("error", "skipped", "processing", "preparing", "unknown"):
        return None
    return status == OUTDATED


SWARM_BINARY = (
    SwarmBinaryDescription(
        key="problem",
        translation_key="swarm_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: bool(swarm_problems(d)),
        attrs_fn=lambda d: {"reasons": swarm_problems(d)},
    ),
)

NODE_BINARY = (
    SwarmBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # A swarm node has a role; a single host has engine details once `docker info` answers.
        value_fn=lambda n: n["info"].get("state") == "ready"
        if n["info"].get("role") or n["info"].get("engine_version")
        else None,
        attrs_fn=lambda n: {"state": n["info"].get("state"), "availability": n["info"].get("availability")},
    ),
)

STACK_BINARY = (
    SwarmBinaryDescription(
        key="update",
        translation_key="update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        value_fn=lambda s: bool(s["updates"]),
        attrs_fn=lambda s: {"services": s["updates"]},
    ),
)

SERVICE_BINARY = (
    SwarmBinaryDescription(
        key="update",
        translation_key="update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        value_fn=_update_value,
        attrs_fn=lambda s: {"image": s["image"], "check": s.get("update")},
    ),
)

CONTAINER_BINARY = SERVICE_BINARY


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwarmContainersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create binary sensors for everything in the swarm, and for things that appear later."""
    coordinator = entry.runtime_data

    def make(kind: str, descriptions: tuple[SwarmBinaryDescription, ...]):
        return lambda key: [SwarmBinarySensor(coordinator, kind, key, d) for d in descriptions]

    entry.async_on_unload(
        add_entities_dynamically(
            coordinator,
            async_add_entities,
            {
                SWARM: make(SWARM, SWARM_BINARY),
                NODE: make(NODE, NODE_BINARY),
                STACK: make(STACK, STACK_BINARY),
                SERVICE: make(SERVICE, SERVICE_BINARY),
                CONTAINER: make(CONTAINER, CONTAINER_BINARY),
            },
        )
    )


class SwarmBinarySensor(SwarmEntity, BinarySensorEntity):
    """A binary sensor reading one value from a snapshot object."""

    entity_description: SwarmBinaryDescription

    def __init__(
        self,
        coordinator: SwarmContainersCoordinator,
        kind: str,
        key: str,
        description: SwarmBinaryDescription,
    ) -> None:
        super().__init__(coordinator, kind, key, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        obj = self.obj
        return self.entity_description.value_fn(obj) if obj is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        obj = self.obj
        if obj is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(obj)
