"""Read-only Portainer API client (GET requests only)."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT


class PortainerError(Exception):
    """Base Portainer client error."""


class PortainerAuthError(PortainerError):
    """The Portainer token was rejected."""


class PortainerConnectionError(PortainerError):
    """Portainer could not be reached."""


class PortainerResponseError(PortainerError):
    """Portainer returned an unexpected response."""


class PortainerClient:
    """Minimal client for the Portainer endpoints this integration reads."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token

    def __repr__(self) -> str:
        return f"PortainerClient(base_url={self._base_url!r})"

    async def _get(self, path: str, extra_headers: dict[str, str] | None = None) -> Any:
        headers = {"X-API-Key": self._api_token, **(extra_headers or {})}
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status in (401, 403):
                    raise PortainerAuthError("Portainer rejected the API token")
                if response.status != 200:
                    raise PortainerResponseError(f"Portainer returned HTTP {response.status}")
                try:
                    return await response.json(content_type=None)
                except ValueError as err:
                    raise PortainerResponseError("Portainer returned malformed JSON") from err
        except PortainerError:
            raise
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise PortainerConnectionError("Unable to connect to Portainer") from err

    async def get_status(self) -> dict[str, Any]:
        """Return Portainer's system status (includes the instance id)."""
        data = await self._get("/api/system/status")
        if not isinstance(data, dict):
            raise PortainerResponseError("Unexpected status response")
        return data

    async def get_endpoints(self) -> list[dict[str, Any]]:
        """Return the Portainer environments that are up."""
        data = await self._get("/api/endpoints")
        if not isinstance(data, list):
            raise PortainerResponseError("Unexpected endpoints response")
        return [e for e in data if isinstance(e, dict) and "Id" in e and e.get("Status") == 1]

    async def get_containers(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return every container the environment reports, across all swarm nodes."""
        data = await self._get(f"/api/endpoints/{endpoint_id}/docker/containers/json?all=1")
        if not isinstance(data, list):
            raise PortainerResponseError("Unexpected containers response")
        return [c for c in data if isinstance(c, dict)]

    async def get_stats(self, endpoint_id: int, container_id: str, node: str) -> dict[str, Any]:
        """Return a one-shot stats sample, routed to the node running the container.

        On a swarm environment Portainer only reaches containers on one node unless
        the request names its target node with X-PortainerAgent-Target.
        """
        data = await self._get(
            f"/api/endpoints/{endpoint_id}/docker/containers/{container_id}/stats"
            "?stream=false&one-shot=true",
            {"X-PortainerAgent-Target": node},
        )
        if not isinstance(data, dict):
            raise PortainerResponseError("Unexpected stats response")
        return data
