"""Data update coordinator for Swarm Containers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    NOT_SWARM_STATUS,
    PortainerAuthError,
    PortainerClient,
    PortainerConnectionError,
    PortainerError,
    PortainerResponseError,
)
from .const import (
    CONF_API_TOKEN,
    CONF_ENDPOINT_ID,
    CONF_IMAGE_CHECK,
    CONF_IMAGE_CHECK_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_STANDALONE,
    CONF_VERIFY_SSL,
    DEFAULT_IMAGE_CHECK_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CONCURRENT_IMAGE_CHECKS,
    MAX_CONCURRENT_STATS,
)
from .parse import build_snapshot, node_name

_LOGGER = logging.getLogger(__name__)

# Image check result when Portainer has no image_status route (Community Edition).
UNSUPPORTED = "unsupported"


def stack_identifier(entry_id: str, stack: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_stack_{stack}")


def service_identifier(entry_id: str, service: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_service_{service}")


def container_identifier(entry_id: str, key: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_container_{key}")


def node_identifier(entry_id: str, node: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_{node}")


def swarm_identifier(entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_swarm")


async def _swarm_list(request: Awaitable[list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
    """Await a swarm-only request; None means the environment isn't a swarm."""
    try:
        return await request
    except PortainerResponseError as err:
        if err.status == NOT_SWARM_STATUS:
            return None
        raise


class SwarmContainersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch nodes, services, tasks and containers, plus stats and image checks."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=interval),
        )
        session = async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_SSL])
        self.client = PortainerClient(session, entry.data["url"], entry.data[CONF_API_TOKEN])
        self.endpoint_id = int(entry.data[CONF_ENDPOINT_ID])
        self.include_standalone: bool = entry.options.get(CONF_STANDALONE, True)
        self._image_check: bool = entry.options.get(CONF_IMAGE_CHECK, True)
        self._image_interval = timedelta(
            seconds=entry.options.get(CONF_IMAGE_CHECK_INTERVAL, DEFAULT_IMAGE_CHECK_INTERVAL)
        )
        self._cpu: dict[str, tuple[int, int, int]] = {}
        self._image_status: dict[str, str] = {}
        self._image_checked: datetime | None = None
        # False once Portainer turns out to have no image check (Community Edition).
        self._image_supported = True
        self._image_task: asyncio.Task | None = None
        # Services and containers seen in the last poll, for the image check.
        self._image_targets: list[tuple[str, str, str | None]] = []
        # The environment's name, used as the node name of a host that isn't in a swarm.
        self._host_name: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            containers, nodes, services, tasks, stacks = await asyncio.gather(
                self.client.get_containers(self.endpoint_id),
                _swarm_list(self.client.get_nodes(self.endpoint_id)),
                _swarm_list(self.client.get_services(self.endpoint_id)),
                _swarm_list(self.client.get_tasks(self.endpoint_id)),
                self.client.get_stacks(),
            )
            swarm = nodes is not None
            host = None if swarm else await self._host()
        except PortainerAuthError as err:
            raise ConfigEntryAuthFailed("Portainer rejected the API token") from err
        except PortainerConnectionError as err:
            raise UpdateFailed(f"Cannot reach Portainer: {err}") from err
        except PortainerError as err:
            raise UpdateFailed(f"Portainer error: {err}") from err

        default_node = None
        if not swarm:
            # Through the Portainer agent the containers already name their host; use
            # that so they and the host details land on the same node.
            agent_node = next(
                (node_name(c) for c in containers if node_name(c) != "unknown"), None
            )
            default_node = agent_node or self._host_name
        counts = await self._resource_counts(default_node)
        stats = await self._stats([c for c in containers if c.get("State") == "running"])

        snapshot, self._cpu = build_snapshot(
            swarm=swarm,
            host_name=default_node,
            host=host,
            containers=containers,
            stats_by_id=stats,
            previous_cpu=self._cpu,
            nodes=nodes,
            services=services,
            tasks=tasks,
            portainer_stacks=[s for s in stacks if s.get("EndpointId") == self.endpoint_id],
            resource_counts=counts,
            image_status=self._image_status,
            image_checked=self._image_checked,
        )
        if not self.include_standalone:
            snapshot["standalone"] = {}
        snapshot["image_check"] = self._image_check
        snapshot["image_check_supported"] = self._image_supported

        self._image_targets = [("service", s["id"], None) for s in snapshot["services"].values()] + [
            ("container", c["full_id"], c["node"]) for c in snapshot["standalone"].values()
        ]
        self._maybe_start_image_check()
        self._remove_stale_devices(snapshot)
        return snapshot

    async def _host(self) -> dict[str, Any] | None:
        """Return `docker info` for a single host, learning its name on first use.

        Missing info is not fatal: the host then shows without engine details.
        """
        if self._host_name is None:
            endpoint = await self.client.get_endpoint(self.endpoint_id)
            self._host_name = str(endpoint.get("Name") or self.endpoint_id)
        try:
            return await self.client.get_info(self.endpoint_id)
        except PortainerAuthError:
            raise
        except PortainerError as err:
            _LOGGER.debug("No docker info for %s: %s", self._host_name, err)
            return None

    async def _stats(self, running: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_STATS)

        async def fetch(container: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            async with semaphore:
                try:
                    stats = await self.client.get_stats(
                        self.endpoint_id, container["Id"], node_name(container)
                    )
                except PortainerAuthError as err:
                    raise ConfigEntryAuthFailed("Portainer rejected the API token") from err
                except PortainerError as err:
                    _LOGGER.debug("No stats for %s: %s", container.get("Id", "?")[:12], err)
                    return container["Id"], None
                return container["Id"], stats

        return dict(await asyncio.gather(*(fetch(c) for c in running)))

    async def _resource_counts(self, default_node: str | None = None) -> dict[str, dict[str, int]]:
        """Count images, volumes and networks per node. Missing counts are not fatal.

        Without the Portainer agent nothing is tagged with a node, so a single host
        (``default_node``) gets them all.
        """
        counts: dict[str, dict[str, int]] = {}
        calls = {
            "images": self.client.get_images(self.endpoint_id),
            "volumes": self.client.get_volumes(self.endpoint_id),
            "networks": self.client.get_networks(self.endpoint_id),
        }
        results = await asyncio.gather(*calls.values(), return_exceptions=True)
        for kind, items in zip(calls, results, strict=True):
            if isinstance(items, PortainerAuthError):
                raise ConfigEntryAuthFailed("Portainer rejected the API token") from items
            if isinstance(items, BaseException):
                _LOGGER.debug("Could not count %s: %s", kind, items)
                continue
            for item in items:
                node = ((item.get("Portainer") or {}).get("Agent") or {}).get("NodeName") or default_node
                if node:
                    node_counts = counts.setdefault(node, {})
                    node_counts[kind] = node_counts.get(kind, 0) + 1
        return counts

    def _maybe_start_image_check(self) -> None:
        if not self._image_check or not self._image_supported:
            return
        if self._image_task and not self._image_task.done():
            return
        now = datetime.now(timezone.utc)
        if self._image_checked and now - self._image_checked < self._image_interval:
            return
        self._image_task = self.config_entry.async_create_background_task(
            self.hass, self._check_images(list(self._image_targets)), "swarm_containers image check"
        )

    async def _check_images(self, targets: list[tuple[str, str, str | None]]) -> None:
        """Ask Portainer whether each image has a newer version in its registry."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_CHECKS)
        results: dict[str, str] = {}

        async def check(kind: str, ident: str, node: str | None) -> None:
            async with semaphore:
                try:
                    if kind == "service":
                        status = await self.client.get_service_image_status(self.endpoint_id, ident)
                    else:
                        status = await self.client.get_container_image_status(
                            self.endpoint_id, ident, node or ""
                        )
                except PortainerResponseError as err:
                    _LOGGER.debug("Image check failed for %s %s: %s", kind, ident[:12], err)
                    status = UNSUPPORTED if err.status == 404 else "error"
                except PortainerError as err:
                    _LOGGER.debug("Image check failed for %s %s: %s", kind, ident[:12], err)
                    status = "error"
                results[f"{kind}:{ident}"] = status

        await asyncio.gather(*(check(*target) for target in targets))
        if results and all(v == UNSUPPORTED for v in results.values()):
            # Portainer Community Edition has no image_status route at all. Stop asking
            # and leave the updates unknown, rather than reporting "0 updates".
            _LOGGER.info(
                "Portainer has no image update check (a Business Edition feature); "
                "image updates will show as unknown"
            )
            self._image_supported = False
            self._image_status = {}
            await self.async_request_refresh()
            return
        self._image_supported = True
        self._image_status = results
        self._image_checked = datetime.now(timezone.utc)
        _LOGGER.debug(
            "Image check done: %d checked, %d outdated",
            len(results),
            sum(v == "outdated" for v in results.values()),
        )
        await self.async_request_refresh()

    async def async_check_images_now(self) -> None:
        """Run the registry check now (used by the refresh button)."""
        if self._image_task and not self._image_task.done():
            return
        await self._check_images(list(self._image_targets))

    def _remove_stale_devices(self, snapshot: dict[str, Any]) -> None:
        """Drop stack, service and container devices that no longer exist."""
        if snapshot["swarm"] and (not snapshot["nodes_total"] or not snapshot["services"]):
            # An empty answer is more likely a Portainer hiccup than an empty swarm.
            return
        if not snapshot["swarm"] and not snapshot["standalone"]:
            # Likewise for a single host that suddenly reports no containers.
            return
        entry_id = self.config_entry.entry_id
        valid = {node_identifier(entry_id, n) for n in snapshot["nodes"]}
        valid.add(swarm_identifier(entry_id))
        valid |= {stack_identifier(entry_id, s) for s in snapshot["stacks"]}
        valid |= {service_identifier(entry_id, s) for s in snapshot["services"]}
        valid |= {container_identifier(entry_id, c) for c in snapshot["standalone"]}
        registry = dr.async_get(self.hass)
        prefixes = (f"{entry_id}_stack_", f"{entry_id}_service_", f"{entry_id}_container_")
        for device in dr.async_entries_for_config_entry(registry, entry_id):
            ours = [i for i in device.identifiers if i[0] == DOMAIN]
            if not ours or any(i in valid for i in ours):
                continue
            if any(i[1].startswith(prefixes) for i in ours):
                registry.async_update_device(device.id, remove_config_entry_id=entry_id)
