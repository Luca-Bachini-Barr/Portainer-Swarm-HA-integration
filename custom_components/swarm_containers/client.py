"""Read-only Portainer API client (GET requests only)."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import IMAGE_CHECK_TIMEOUT, REQUEST_TIMEOUT


class PortainerError(Exception):
    """Base Portainer client error."""


class PortainerAuthError(PortainerError):
    """The Portainer token was rejected."""


class PortainerConnectionError(PortainerError):
    """Portainer could not be reached."""


class PortainerResponseError(PortainerError):
    """Portainer returned an unexpected response."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# What Docker answers swarm requests with when the engine isn't part of a swarm.
NOT_SWARM_STATUS = 503


class PortainerClient:
    """Minimal client for the Portainer endpoints this integration reads."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token

    def __repr__(self) -> str:
        return f"PortainerClient(base_url={self._base_url!r})"

    async def _get(
        self,
        path: str,
        extra_headers: dict[str, str] | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        headers = {"X-API-Key": self._api_token, **(extra_headers or {})}
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status in (401, 403):
                    raise PortainerAuthError("Portainer rejected the API token")
                if response.status != 200:
                    raise PortainerResponseError(
                        f"Portainer returned HTTP {response.status}", response.status
                    )
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

    async def _get_list(self, path: str, key: str | None = None) -> list[dict[str, Any]]:
        data = await self._get(path)
        if key is not None and isinstance(data, dict):
            data = data.get(key)
        if not isinstance(data, list):
            raise PortainerResponseError(f"Unexpected response from {path.split('?')[0]}")
        return [item for item in data if isinstance(item, dict)]

    async def get_endpoint(self, endpoint_id: int) -> dict[str, Any]:
        """Return one Portainer environment."""
        data = await self._get(f"/api/endpoints/{endpoint_id}")
        if not isinstance(data, dict):
            raise PortainerResponseError("Unexpected endpoint response")
        return data

    async def get_info(self, endpoint_id: int) -> dict[str, Any]:
        """Return `docker info` for the environment's engine."""
        data = await self._get(f"/api/endpoints/{endpoint_id}/docker/info")
        if not isinstance(data, dict):
            raise PortainerResponseError("Unexpected info response")
        return data

    async def get_nodes(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return the swarm nodes."""
        return await self._get_list(f"/api/endpoints/{endpoint_id}/docker/nodes")

    async def get_services(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return the swarm services with their running/desired task counts."""
        return await self._get_list(f"/api/endpoints/{endpoint_id}/docker/services?status=true")

    async def get_tasks(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return the swarm tasks, including recently finished and failed ones."""
        return await self._get_list(f"/api/endpoints/{endpoint_id}/docker/tasks")

    async def get_stacks(self) -> list[dict[str, Any]]:
        """Return the stacks Portainer manages."""
        return await self._get_list("/api/stacks")

    async def get_images(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return images on every node (each tagged with its node by the agent)."""
        return await self._get_list(f"/api/endpoints/{endpoint_id}/docker/images/json")

    async def get_volumes(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return volumes on every node."""
        return await self._get_list(f"/api/endpoints/{endpoint_id}/docker/volumes", "Volumes")

    async def get_networks(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Return networks on every node."""
        return await self._get_list(f"/api/endpoints/{endpoint_id}/docker/networks")

    async def get_service_image_status(self, endpoint_id: int, service_id: str) -> str:
        """Return Portainer's registry check for a service image (e.g. updated/outdated)."""
        data = await self._get(
            f"/api/docker/{endpoint_id}/services/{service_id}/image_status",
            timeout=IMAGE_CHECK_TIMEOUT,
        )
        return str((data or {}).get("Status") or "unknown")

    async def get_container_image_status(self, endpoint_id: int, container_id: str, node: str) -> str:
        """Return Portainer's registry check for a standalone container's image."""
        data = await self._get(
            f"/api/docker/{endpoint_id}/containers/{container_id}/image_status",
            {"X-PortainerAgent-Target": node},
            IMAGE_CHECK_TIMEOUT,
        )
        return str((data or {}).get("Status") or "unknown")

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
