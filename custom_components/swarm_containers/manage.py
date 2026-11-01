"""Management actions: restart, redeploy, stop/start, scale and remove through Portainer.

Every action needs the "Allow management actions" option on the config entry that
owns the target. Each one re-reads what it changes from Portainer just before
changing it, so a stale snapshot never overwrites a newer spec.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .client import PortainerError
from .const import CONF_MANAGEMENT

if TYPE_CHECKING:
    from .coordinator import SwarmContainersCoordinator

_LOGGER = logging.getLogger(__name__)

STACK = "stack"
SERVICE = "service"
CONTAINER = "container"
NODE = "node"

NODE_AVAILABILITY = ("active", "pause", "drain")

# Images that carry the integration's own connection. Stopping or removing them
# would leave nothing to start them again from Home Assistant.
PORTAINER_IMAGES = ("portainer/portainer", "portainer/agent")


def management_enabled(coordinator: SwarmContainersCoordinator) -> bool:
    return bool(coordinator.config_entry.options.get(CONF_MANAGEMENT, False))


def _is_portainer(image: str | None) -> bool:
    return any(p in (image or "") for p in PORTAINER_IMAGES)


class Manager:
    """Runs management actions against one Portainer environment."""

    def __init__(self, coordinator: SwarmContainersCoordinator) -> None:
        self.coordinator = coordinator
        self.client = coordinator.client
        self.endpoint = coordinator.endpoint_id

    @property
    def data(self) -> dict[str, Any]:
        return self.coordinator.data

    # ----- lookups -----

    def _service(self, name: str) -> dict[str, Any]:
        service = self.data["services"].get(name)
        if not service:
            raise ServiceValidationError(f"Service {name} is not in the swarm any more")
        return service

    def _stack(self, name: str) -> dict[str, Any]:
        stack = self.data["stacks"].get(name)
        if stack is None:
            raise ServiceValidationError(f"Stack {name} is not known any more")
        return stack

    def _managed_stack(self, name: str) -> dict[str, Any]:
        stack = self._stack(name)
        if not stack.get("portainer_id"):
            raise ServiceValidationError(
                f"Stack {name} was not deployed through Portainer, so Portainer can't redeploy, stop or start it"
            )
        return stack

    def _container(self, key: str) -> dict[str, Any]:
        container = self.data["standalone"].get(key)
        if not container:
            raise ServiceValidationError(f"Container {key} is not known any more")
        return container

    def _stack_containers(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.data["standalone"].values() if c.get("stack") == name]

    def _stack_has_portainer(self, name: str) -> bool:
        if self.data.get("swarm", True):
            return any(
                _is_portainer(self.data["services"].get(s, {}).get("image"))
                for s in self._stack(name).get("services", [])
            )
        return any(_is_portainer(c.get("image")) for c in self._stack_containers(name))

    # ----- actions -----

    async def restart(self, kind: str, key: str) -> None:
        """Restart a service (rolling, like `docker service update --force`), a stack or a container."""
        if kind == SERVICE:
            await self._force_update(self._service(key))
        elif kind == CONTAINER:
            container = self._container(key)
            await self._call(self.client.container_action(self.endpoint, container["full_id"], "restart", container["node"]))
        elif kind == STACK:
            if self.data.get("swarm", True):
                names = self._stack(key).get("services") or []
                if not names:
                    raise ServiceValidationError(f"Stack {key} has no running services; start it instead")
                await self._gather(self._force_update(self._service(n)) for n in names)
            else:
                containers = self._stack_containers(key)
                if not containers:
                    raise ServiceValidationError(f"Stack {key} has no containers; start it instead")
                await self._gather(
                    self.client.container_action(self.endpoint, c["full_id"], "restart", c["node"])
                    for c in containers
                )
        else:
            raise ServiceValidationError("Restart works on a stack, service or container device")

    async def redeploy(self, stack_name: str, pull_image: bool, prune: bool) -> None:
        """Deploy a stack again from the compose file Portainer holds."""
        stack = self._managed_stack(stack_name)
        if stack.get("git"):
            raise ServiceValidationError(
                f"Stack {stack_name} is deployed from git; redeploy it from Portainer (pull and redeploy)"
            )
        sid = int(stack["portainer_id"])
        details, content = await self._call(
            asyncio.gather(self.client.get_stack(sid), self.client.get_stack_file(sid))
        )
        await self._call(
            self.client.redeploy_stack(sid, self.endpoint, content, details.get("Env") or [], pull_image, prune)
        )

    async def set_running(self, kind: str, key: str, running: bool) -> None:
        """Start or stop a Portainer stack or a container."""
        action = "start" if running else "stop"
        if kind == STACK:
            stack = self._managed_stack(key)
            if not running and self._stack_has_portainer(key):
                raise ServiceValidationError(f"Stack {key} runs Portainer itself; stop it from the host instead")
            await self._call(self.client.stack_action(int(stack["portainer_id"]), self.endpoint, action))
        elif kind == CONTAINER:
            container = self._container(key)
            if not running and _is_portainer(container.get("image")):
                raise ServiceValidationError(f"{container['name']} runs Portainer itself; stop it from the host instead")
            await self._call(self.client.container_action(self.endpoint, container["full_id"], action, container["node"]))
        else:
            raise ServiceValidationError(f"{action.capitalize()} works on a stack or container device; scale a service instead")

    async def scale(self, name: str, replicas: int) -> None:
        service = self._service(name)
        if replicas == 0 and _is_portainer(service.get("image")):
            raise ServiceValidationError(f"{name} runs Portainer itself; it can't be scaled to 0 from here")

        def change(spec: dict[str, Any]) -> None:
            mode = spec.get("Mode") or {}
            if "Replicated" not in mode:
                raise ServiceValidationError(f"{name} is a global service; it runs one task per node and can't be scaled")
            mode["Replicated"]["Replicas"] = replicas

        await self._update_spec(service, change)

    async def rollback(self, name: str) -> None:
        """Go back to the service's previous spec, like `docker service rollback`."""
        service = self._service(name)
        current = await self._call(self.client.get_service(self.endpoint, service["id"]))
        if not current.get("PreviousSpec"):
            raise ServiceValidationError(f"{name} has no previous spec to roll back to")
        await self._call(
            self.client.update_service(
                self.endpoint, service["id"], current["Version"]["Index"], current["Spec"], rollback=True
            )
        )

    async def remove(self, name: str, recreate: bool) -> None:
        """Remove a service; with ``recreate``, redeploy its stack so it comes back fresh."""
        service = self._service(name)
        if _is_portainer(service.get("image")):
            raise ServiceValidationError(f"{name} runs Portainer itself; remove it from the host instead")
        if recreate:
            # Check the stack can be redeployed before removing anything.
            stack = self._managed_stack(service["stack"])
            if stack.get("git"):
                raise ServiceValidationError(f"Stack {service['stack']} is deployed from git; recreate it from Portainer")
        await self._call(self.client.remove_service(self.endpoint, service["id"]))
        if recreate:
            # Docker removes the old tasks in the background; a deploy that comes too soon
            # can clash with the network attachments that are still being cleaned up.
            await asyncio.sleep(3)
            await self.redeploy(service["stack"], pull_image=False, prune=False)

    async def set_node_availability(self, node: str, availability: str) -> None:
        info = (self.data["nodes"].get(node) or {}).get("info") or {}
        if not info.get("id"):
            raise ServiceValidationError(f"{node} is not a swarm node")
        current = await self._call(self.client.get_node(self.endpoint, info["id"]))
        spec = copy.deepcopy(current["Spec"])
        spec["Availability"] = availability
        await self._call(self.client.update_node(self.endpoint, info["id"], current["Version"]["Index"], spec))

    # ----- helpers -----

    async def _force_update(self, service: dict[str, Any]) -> None:
        def change(spec: dict[str, Any]) -> None:
            template = spec.setdefault("TaskTemplate", {})
            template["ForceUpdate"] = int(template.get("ForceUpdate") or 0) + 1

        await self._update_spec(service, change)

    async def _update_spec(self, service: dict[str, Any], change) -> None:
        current = await self._call(self.client.get_service(self.endpoint, service["id"]))
        spec = copy.deepcopy(current["Spec"])
        change(spec)
        await self._call(self.client.update_service(self.endpoint, service["id"], current["Version"]["Index"], spec))

    async def _gather(self, calls) -> None:
        results = await asyncio.gather(*calls, return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        for err in errors:
            if not isinstance(err, (PortainerError, HomeAssistantError)):
                raise err
        if errors:
            raise HomeAssistantError(
                f"{len(errors)} of {len(results)} failed: " + "; ".join(str(e) for e in errors[:3])
            ) from errors[0]

    async def _call(self, awaitable):
        try:
            return await awaitable
        except PortainerError as err:
            raise HomeAssistantError(f"Portainer: {err}") from err


def manager_for(coordinator: SwarmContainersCoordinator) -> Manager:
    if not management_enabled(coordinator):
        raise ServiceValidationError(
            f"Management actions are turned off for {coordinator.config_entry.title}; "
            "turn on \"Allow management actions\" in the integration options"
        )
    return Manager(coordinator)
