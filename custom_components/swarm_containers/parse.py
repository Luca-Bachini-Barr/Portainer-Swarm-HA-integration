"""Turn Portainer/Docker API responses into per-node container tables.

Pure functions with no Home Assistant imports so they can be unit tested.
"""

from __future__ import annotations

from typing import Any

NO_STACK = "standalone"
INGRESS_NETWORKS = {"ingress", "host", "bridge", "none"}


def container_name(container: dict[str, Any]) -> str:
    """Return a readable name: the swarm service name, else the container name."""
    labels = container.get("Labels") or {}
    service = labels.get("com.docker.swarm.service.name")
    if service:
        slot = labels.get("com.docker.swarm.task.name", "").split(".")
        # task name looks like "<service>.<slot>.<id>"; show the slot for replicas.
        if len(slot) >= 3 and slot[1].isdigit():
            return service if slot[1] == "1" else f"{service}.{slot[1]}"
        return str(service)
    names = container.get("Names") or []
    return str(names[0]).lstrip("/") if names else str(container.get("Id", "unknown"))[:12]


def short_image(image: str) -> str:
    """Drop the @sha256 digest so the image reads as repo:tag."""
    return str(image).split("@", 1)[0]


def node_name(container: dict[str, Any]) -> str:
    """Return the node (Pi) a container runs on, as reported by the Portainer agent."""
    agent = (container.get("Portainer") or {}).get("Agent") or {}
    return str(agent.get("NodeName") or "unknown")


def health_state(container: dict[str, Any]) -> str:
    """Return healthy / unhealthy / starting / none for a container."""
    health = (container.get("Health") or {}).get("Status")
    if health:
        return str(health)
    status = str(container.get("Status", "")).lower()
    if "(unhealthy)" in status:
        return "unhealthy"
    if "(health: starting)" in status:
        return "starting"
    if "(healthy)" in status:
        return "healthy"
    return "none"


def uptime_text(container: dict[str, Any]) -> str:
    """Return the human uptime from the Docker status text, e.g. '2 hours'."""
    status = str(container.get("Status", ""))
    if status.startswith("Up "):
        return status[3:].split(" (", 1)[0]
    return status or str(container.get("State", "unknown"))


def addresses(container: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
    """Return (primary stack IP, {network: ip}) for a container.

    The primary IP prefers the stack's own overlay network over the shared
    ingress network, which is the address other services in the stack use.
    """
    networks = ((container.get("NetworkSettings") or {}).get("Networks")) or {}
    all_ips = {
        name: str(data.get("IPAddress"))
        for name, data in networks.items()
        if data.get("IPAddress")
    }
    primary = next(
        (ip for name, ip in all_ips.items() if name not in INGRESS_NETWORKS), None
    )
    if primary is None and all_ips:
        primary = next(iter(all_ips.values()))
    return primary, all_ips


def memory_from_stats(stats: dict[str, Any] | None) -> tuple[float | None, float | None]:
    """Return (used MiB, percent of limit) like `docker stats` does.

    Docker's own figure subtracts the page cache (inactive file memory).
    """
    if not stats:
        return None, None
    mem = stats.get("memory_stats") or {}
    usage = mem.get("usage")
    if usage is None:
        return None, None
    detail = mem.get("stats") or {}
    cache = detail.get("inactive_file", detail.get("total_inactive_file", detail.get("cache", 0)))
    used = max(0, usage - (cache or 0))
    limit = mem.get("limit") or 0
    percent = round(used / limit * 100, 1) if limit else None
    return round(used / 1048576, 1), percent


def build_tables(
    containers: list[dict[str, Any]],
    stats_by_id: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """Group containers by node and build display rows plus totals."""
    nodes: dict[str, list[dict[str, Any]]] = {}
    for container in containers:
        mem_mib, mem_pct = memory_from_stats(stats_by_id.get(container.get("Id", "")))
        ip, networks = addresses(container)
        labels = container.get("Labels") or {}
        nodes.setdefault(node_name(container), []).append(
            {
                "name": container_name(container),
                "stack": labels.get("com.docker.stack.namespace") or NO_STACK,
                "image": short_image(container.get("Image", "")),
                "health": health_state(container),
                "state": container.get("State", "unknown"),
                "uptime": uptime_text(container),
                "memory_mib": mem_mib,
                "memory_percent": mem_pct,
                "ip": ip,
                "networks": networks,
                "id": str(container.get("Id", ""))[:12],
            }
        )
    result: dict[str, Any] = {}
    for name, rows in nodes.items():
        rows.sort(key=lambda r: (r["stack"] == NO_STACK, r["stack"], r["name"]))
        result[name] = {
            "containers": rows,
            "total": len(rows),
            "running": sum(r["state"] == "running" for r in rows),
            "unhealthy": sum(r["health"] == "unhealthy" for r in rows),
            "memory_mib": round(sum(r["memory_mib"] or 0 for r in rows), 1),
        }
    return result
