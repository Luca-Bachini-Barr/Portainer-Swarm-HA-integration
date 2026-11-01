"""Home Assistant actions for managing stacks, services, containers and nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN
from .coordinator import (
    SwarmContainersCoordinator,
    container_identifier,
    node_identifier,
    service_identifier,
    stack_identifier,
)
from .manage import CONTAINER, NODE, NODE_AVAILABILITY, SERVICE, STACK, Manager, manager_for

ATTR_DEVICE_ID = "device_id"

DEVICES = vol.All(cv.ensure_list, [cv.string])
BASE = {vol.Required(ATTR_DEVICE_ID): DEVICES}


def resolve(hass: HomeAssistant, device_id: str) -> tuple[SwarmContainersCoordinator, str, str]:
    """Return the coordinator, kind and key of the object a device stands for."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device {device_id}")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(f"{entry.title} is not loaded")
        coordinator: SwarmContainersCoordinator = entry.runtime_data
        data = coordinator.data
        candidates = (
            [(STACK, k, stack_identifier(entry_id, k)) for k in data["stacks"]]
            + [(SERVICE, k, service_identifier(entry_id, k)) for k in data["services"]]
            + [(CONTAINER, k, container_identifier(entry_id, k)) for k in data["standalone"]]
            + [(NODE, k, node_identifier(entry_id, k)) for k in data["nodes"]]
        )
        for kind, key, ident in candidates:
            if ident in device.identifiers:
                return coordinator, kind, key
    raise ServiceValidationError(
        f"{device.name_by_user or device.name} is not a Swarm Containers stack, service, container or node"
    )


def _handler(
    hass: HomeAssistant,
    kinds: tuple[str, ...],
    run: Callable[[Manager, str, str, ServiceCall], Awaitable[None]],
    service_means_stack: bool = False,
) -> Callable[[ServiceCall], Awaitable[None]]:
    """Build an action handler that runs ``run`` once for each targeted device."""

    async def handle(call: ServiceCall) -> None:
        targets = []
        # Check every target first, so a bad one doesn't leave the rest half done.
        for device_id in call.data[ATTR_DEVICE_ID]:
            coordinator, kind, key = resolve(hass, device_id)
            if kind == SERVICE and service_means_stack:
                # Redeploying accepts one of the stack's services and redeploys its stack.
                kind, key = STACK, coordinator.data["services"][key]["stack"]
            if kind not in kinds:
                raise ServiceValidationError(
                    f"{call.service} works on {' / '.join(kinds)} devices, not a {kind} ({key})"
                )
            targets.append((coordinator, manager_for(coordinator), kind, key))
        try:
            for _coordinator, manager, kind, key in targets:
                await run(manager, kind, key, call)
        finally:
            for coordinator in {id(t[0]): t[0] for t in targets}.values():
                await coordinator.async_request_refresh()

    return handle


async def _restart(m: Manager, kind: str, key: str, _call: ServiceCall) -> None:
    await m.restart(kind, key)


async def _redeploy(m: Manager, _kind: str, key: str, call: ServiceCall) -> None:
    await m.redeploy(key, call.data["pull_image"], call.data["prune"])


async def _start(m: Manager, kind: str, key: str, _call: ServiceCall) -> None:
    await m.set_running(kind, key, True)


async def _stop(m: Manager, kind: str, key: str, _call: ServiceCall) -> None:
    await m.set_running(kind, key, False)


async def _scale(m: Manager, _kind: str, key: str, call: ServiceCall) -> None:
    await m.scale(key, call.data["replicas"])


async def _rollback(m: Manager, _kind: str, key: str, _call: ServiceCall) -> None:
    await m.rollback(key)


async def _remove(m: Manager, _kind: str, key: str, call: ServiceCall) -> None:
    await m.remove(key, call.data["recreate"])


async def _availability(m: Manager, _kind: str, key: str, call: ServiceCall) -> None:
    await m.set_node_availability(key, call.data["availability"])


# name: (device kinds it accepts, handler, extra schema)
ACTIONS: dict[str, tuple[tuple[str, ...], Callable, dict[Any, Any]]] = {
    "restart": ((STACK, SERVICE, CONTAINER), _restart, {}),
    "redeploy_stack": (
        (STACK,),
        _redeploy,
        {vol.Optional("pull_image", default=True): cv.boolean, vol.Optional("prune", default=False): cv.boolean},
    ),
    "start": ((STACK, CONTAINER), _start, {}),
    "stop": ((STACK, CONTAINER), _stop, {}),
    "scale_service": ((SERVICE,), _scale, {vol.Required("replicas"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100))}),
    "rollback_service": ((SERVICE,), _rollback, {}),
    "remove_service": ((SERVICE,), _remove, {vol.Optional("recreate", default=False): cv.boolean}),
    "set_node_availability": ((NODE,), _availability, {vol.Required("availability"): vol.In(NODE_AVAILABILITY)}),
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the management actions (they check the entry's option when called)."""
    for name, (kinds, run, extra) in ACTIONS.items():
        hass.services.async_register(
            DOMAIN,
            name,
            _handler(hass, kinds, run, service_means_stack=name == "redeploy_stack"),
            schema=vol.Schema({**BASE, **extra}),
        )
