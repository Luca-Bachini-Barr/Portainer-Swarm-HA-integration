"""Data update coordinator for Swarm Containers."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CONCURRENT_STATS,
)
from .parse import build_tables, node_name

_LOGGER = logging.getLogger(__name__)


class SwarmContainersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch every container, its node and its memory use."""

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

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            containers = await self.client.get_containers(self.endpoint_id)
        except PortainerAuthError as err:
            raise ConfigEntryAuthFailed("Portainer rejected the API token") from err
        except PortainerConnectionError as err:
            raise UpdateFailed(f"Cannot reach Portainer: {err}") from err
        except PortainerError as err:
            raise UpdateFailed(f"Portainer error: {err}") from err

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

        running = [c for c in containers if c.get("State") == "running"]
        results = await asyncio.gather(*(fetch(c) for c in running))
        return build_tables(containers, dict(results))
