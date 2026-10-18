"""Data update coordinator for Swarm Containers."""

from __future__ import annotations

import asyncio
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
    PortainerAuthError,
    PortainerClient,
    PortainerConnectionError,
    PortainerError,
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
        self._image_task: asyncio.Task | None = None
        # Services and containers seen in the last poll, for the image check.
        self._image_targets: list[tuple[str, str, str | None]] = []

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            containers, nodes, services, tasks, stacks = await asyncio.gather(
                self.client.get_containers(self.endpoint_id),
                self.client.get_nodes(self.endpoint_id),
                self.client.get_services(self.endpoint_id),
                self.client.get_tasks(self.endpoint_id),
                self.client.get_stacks(),
            )
        except PortainerAuthError as err:
            raise ConfigEntryAuthFailed("Portainer rejected the API token") from err
        except PortainerConnectionError as err:
            raise UpdateFailed(f"Cannot reach Portainer: {err}") from err
        except PortainerError as err:
            raise UpdateFailed(f"Portainer error: {err}") from err

        counts = await self._resource_counts()
        stats = await self._stats([c for c in containers if c.get("State") == "running"])

        snapshot, self._cpu = build_snapshot(
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

        self._image_targets = [("service", s["id"], None) for s in snapshot["services"].values()] + [
            ("container", c["full_id"], c["node"]) for c in snapshot["standalone"].values()
        ]
        self._maybe_start_image_check()
        self._remove_stale_devices(snapshot)
        return snapshot

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

    async def _resource_counts(self) -> dict[str, dict[str, int]]:
        """Count images, volumes and networks per node. Missing counts are not fatal."""
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
                node = ((item.get("Portainer") or {}).get("Agent") or {}).get("NodeName")
                if node:
                    node_counts = counts.setdefault(node, {})
                    node_counts[kind] = node_counts.get(kind, 0) + 1
        return counts

    def _maybe_start_image_check(self) -> None:
        if not self._image_check or (self._image_task and not self._image_task.done()):
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
                except PortainerError as err:
                    _LOGGER.debug("Image check failed for %s %s: %s", kind, ident[:12], err)
                    status = "error"
                results[f"{kind}:{ident}"] = status

        await asyncio.gather(*(check(*target) for target in targets))
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
        if not snapshot["nodes_total"] or not snapshot["services"]:
            # An empty answer is more likely a Portainer hiccup than an empty swarm.
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
