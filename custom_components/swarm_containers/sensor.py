"""Sensors for the swarm, its nodes, stacks, services and standalone containers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SwarmContainersConfigEntry
from .coordinator import SwarmContainersCoordinator
from .entity import CONTAINER, NODE, SERVICE, STACK, SWARM, SwarmEntity, add_entities_dynamically
from .parse import SERVICE_STATES, STACK_STATES

CONTAINER_STATES = ["created", "running", "paused", "restarting", "removing", "exited", "dead"]
HEALTH_STATES = ["healthy", "unhealthy", "starting", "none"]


@dataclass(frozen=True, kw_only=True)
class SwarmSensorDescription(SensorEntityDescription):
    """Describes a sensor and how to read it from its snapshot object."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _pct(**kwargs: Any) -> dict[str, Any]:
    return {
        "native_unit_of_measurement": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "suggested_display_precision": 1,
        **kwargs,
    }


def _mib(**kwargs: Any) -> dict[str, Any]:
    return {
        "device_class": SensorDeviceClass.DATA_SIZE,
        "native_unit_of_measurement": UnitOfInformation.MEBIBYTES,
        "state_class": SensorStateClass.MEASUREMENT,
        "suggested_display_precision": 0,
        **kwargs,
    }


def _count(**kwargs: Any) -> dict[str, Any]:
    return {"state_class": SensorStateClass.MEASUREMENT, **kwargs}


SWARM_SENSORS: tuple[SwarmSensorDescription, ...] = (
    SwarmSensorDescription(
        key="nodes_ready",
        translation_key="nodes_ready",
        icon="mdi:server-network",
        value_fn=lambda d: d["nodes_ready"],
        attrs_fn=lambda d: {
            "total": d["nodes_total"],
            "managers": d["managers"],
            "leader": d["leader"],
            "nodes": {
                name: {k: t["info"].get(k) for k in ("role", "state", "availability", "leader", "address")}
                for name, t in sorted(d["nodes"].items())
            },
        },
        **_count(),
    ),
    SwarmSensorDescription(
        key="services_running",
        translation_key="services_running",
        icon="mdi:cube-outline",
        value_fn=lambda d: d["services_running"],
        attrs_fn=lambda d: {"total": d["services_total"], "problems": d["problem_services"]},
        **_count(),
    ),
    SwarmSensorDescription(
        key="stacks",
        translation_key="stacks",
        icon="mdi:layers-triple",
        value_fn=lambda d: len(d["stacks"]),
        attrs_fn=lambda d: {
            state: sorted(n for n, s in d["stacks"].items() if s["state"] == state)
            for state in STACK_STATES
        },
        **_count(),
    ),
    SwarmSensorDescription(
        key="tasks_running",
        translation_key="tasks_running",
        icon="mdi:format-list-checks",
        value_fn=lambda d: d["tasks_running"],
        **_count(),
    ),
    SwarmSensorDescription(
        key="updates_available",
        translation_key="updates_available",
        icon="mdi:package-up",
        value_fn=lambda d: len(d["updates"]) if d["updates_checked"] else None,
        attrs_fn=lambda d: {
            "updates": d["updates"],
            "last_checked": d["updates_checked"],
            "enabled": d["image_check"],
        },
        **_count(),
    ),
    SwarmSensorDescription(
        key="task_failures",
        translation_key="task_failures",
        icon="mdi:alert-circle-outline",
        value_fn=lambda d: d["failures_24h"],
        attrs_fn=lambda d: {
            "services": {
                s["name"]: s["failures_24h"] for s in d["services"].values() if s["failures_24h"]
            }
        },
        **_count(),
    ),
)

NODE_SENSORS: tuple[SwarmSensorDescription, ...] = (
    SwarmSensorDescription(
        key="containers",
        translation_key="containers",
        icon="mdi:docker",
        value_fn=lambda n: n["running"],
        attrs_fn=lambda n: {
            "total": n["total"],
            "stopped": n["stopped"],
            "unhealthy": n["unhealthy"],
            "containers": n["containers"],
        },
        **_count(),
    ),
    SwarmSensorDescription(
        key="container_memory",
        translation_key="container_memory",
        value_fn=lambda n: n["memory_mib"],
        attrs_fn=lambda n: {"memory_total_mib": n["info"].get("memory_total_mib")},
        **_mib(),
    ),
    SwarmSensorDescription(
        key="container_memory_percent",
        translation_key="container_memory_percent",
        icon="mdi:memory",
        value_fn=lambda n: n["memory_percent"],
        **_pct(),
    ),
    SwarmSensorDescription(
        key="container_cpu",
        translation_key="container_cpu",
        icon="mdi:cpu-64-bit",
        value_fn=lambda n: n["cpu_percent"],
        attrs_fn=lambda n: {"cpus": n["info"].get("cpus"), "top": n["top"]},
        **_pct(),
    ),
    SwarmSensorDescription(
        key="tasks",
        translation_key="tasks",
        icon="mdi:format-list-checks",
        value_fn=lambda n: n["tasks"],
        **_count(),
    ),
    SwarmSensorDescription(
        key="role",
        translation_key="role",
        icon="mdi:crown-outline",
        device_class=SensorDeviceClass.ENUM,
        options=["manager", "worker", "unknown"],
        value_fn=lambda n: n["info"].get("role", "unknown"),
        attrs_fn=lambda n: {
            k: n["info"].get(k)
            for k in (
                "leader",
                "availability",
                "reachability",
                "address",
                "engine_version",
                "os",
                "architecture",
                "cpus",
                "memory_total_mib",
            )
        },
    ),
    SwarmSensorDescription(
        key="images",
        translation_key="images",
        icon="mdi:layers-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n: n["counts"].get("images"),
        **_count(),
    ),
    SwarmSensorDescription(
        key="volumes",
        translation_key="volumes",
        icon="mdi:database-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n: n["counts"].get("volumes"),
        **_count(),
    ),
    SwarmSensorDescription(
        key="networks",
        translation_key="networks",
        icon="mdi:lan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n: n["counts"].get("networks"),
        **_count(),
    ),
)

STACK_SENSORS: tuple[SwarmSensorDescription, ...] = (
    SwarmSensorDescription(
        key="status",
        translation_key="stack_status",
        icon="mdi:layers-triple",
        device_class=SensorDeviceClass.ENUM,
        options=list(STACK_STATES),
        value_fn=lambda s: s["state"],
        attrs_fn=lambda s: {
            "services": s["services"],
            "services_running": s["services_running"],
            "containers": s["containers"],
            "desired": s["desired"],
            "nodes": s["nodes"],
            "updates": s["updates"],
            "unhealthy": s["unhealthy"],
            "failures_24h": s["failures_24h"],
            "managed": s["managed"],
            "git": s["git"],
            "portainer_id": s["portainer_id"],
            "updated": s["updated"],
        },
    ),
    SwarmSensorDescription(
        key="containers",
        translation_key="stack_containers",
        icon="mdi:docker",
        value_fn=lambda s: s["containers"],
        attrs_fn=lambda s: {"desired": s["desired"]},
        **_count(),
    ),
    SwarmSensorDescription(
        key="cpu",
        translation_key="cpu",
        icon="mdi:cpu-64-bit",
        value_fn=lambda s: s["cpu_percent"],
        **_pct(),
    ),
    SwarmSensorDescription(
        key="memory",
        translation_key="memory",
        value_fn=lambda s: s["memory_mib"],
        **_mib(),
    ),
)

SERVICE_SENSORS: tuple[SwarmSensorDescription, ...] = (
    SwarmSensorDescription(
        key="status",
        translation_key="service_status",
        icon="mdi:cube-outline",
        device_class=SensorDeviceClass.ENUM,
        options=list(SERVICE_STATES),
        value_fn=lambda s: s["state"],
        attrs_fn=lambda s: {
            k: s[k]
            for k in (
                "stack",
                "image",
                "mode",
                "running",
                "desired",
                "nodes",
                "tasks",
                "unhealthy",
                "update_state",
                "update_message",
                "constraints",
                "ports",
                "last_failure",
                "created",
                "updated",
            )
        },
    ),
    SwarmSensorDescription(
        key="replicas",
        translation_key="replicas",
        icon="mdi:content-copy",
        value_fn=lambda s: s["running"],
        attrs_fn=lambda s: {"desired": s["desired"], "mode": s["mode"]},
        **_count(),
    ),
    SwarmSensorDescription(
        key="cpu",
        translation_key="cpu",
        icon="mdi:cpu-64-bit",
        value_fn=lambda s: s["cpu_percent"],
        **_pct(),
    ),
    SwarmSensorDescription(
        key="memory",
        translation_key="memory",
        value_fn=lambda s: s["memory_mib"],
        **_mib(),
    ),
    SwarmSensorDescription(
        key="task_failures",
        translation_key="task_failures",
        icon="mdi:alert-circle-outline",
        value_fn=lambda s: s["failures_24h"],
        attrs_fn=lambda s: {"last_failure": s["last_failure"]},
        **_count(),
    ),
)

CONTAINER_SENSORS: tuple[SwarmSensorDescription, ...] = (
    SwarmSensorDescription(
        key="state",
        translation_key="container_state",
        icon="mdi:docker",
        device_class=SensorDeviceClass.ENUM,
        options=CONTAINER_STATES,
        value_fn=lambda c: c["state"] if c["state"] in CONTAINER_STATES else None,
        attrs_fn=lambda c: {
            k: c[k] for k in ("node", "stack", "image", "uptime", "ip", "networks", "id")
        },
    ),
    SwarmSensorDescription(
        key="health",
        translation_key="health",
        icon="mdi:heart-pulse",
        device_class=SensorDeviceClass.ENUM,
        options=HEALTH_STATES,
        value_fn=lambda c: c["health"] if c["health"] in HEALTH_STATES else None,
    ),
    SwarmSensorDescription(
        key="cpu",
        translation_key="cpu",
        icon="mdi:cpu-64-bit",
        value_fn=lambda c: c["cpu_percent"],
        **_pct(),
    ),
    SwarmSensorDescription(
        key="memory",
        translation_key="memory",
        value_fn=lambda c: c["memory_mib"],
        attrs_fn=lambda c: {"percent_of_limit": c["memory_percent"]},
        **_mib(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwarmContainersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for everything in the swarm, and for things that appear later."""
    coordinator = entry.runtime_data

    def make(kind: str, descriptions: tuple[SwarmSensorDescription, ...]):
        return lambda key: [SwarmSensor(coordinator, kind, key, d) for d in descriptions]

    entry.async_on_unload(
        add_entities_dynamically(
            coordinator,
            async_add_entities,
            {
                SWARM: make(SWARM, SWARM_SENSORS),
                NODE: make(NODE, NODE_SENSORS),
                STACK: make(STACK, STACK_SENSORS),
                SERVICE: make(SERVICE, SERVICE_SENSORS),
                CONTAINER: make(CONTAINER, CONTAINER_SENSORS),
            },
        )
    )


class SwarmSensor(SwarmEntity, SensorEntity):
    """A sensor reading one value from a snapshot object."""

    entity_description: SwarmSensorDescription
    # Tables and lists are large and change often, so keep them out of the recorder.
    _unrecorded_attributes = frozenset({"containers", "updates", "top", "tasks", "nodes", "services"})

    def __init__(
        self,
        coordinator: SwarmContainersCoordinator,
        kind: str,
        key: str,
        description: SwarmSensorDescription,
    ) -> None:
        super().__init__(coordinator, kind, key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        obj = self.obj
        return self.entity_description.value_fn(obj) if obj is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        obj = self.obj
        if obj is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(obj)
