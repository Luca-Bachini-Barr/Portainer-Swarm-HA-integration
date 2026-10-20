"""Turn Portainer/Docker API responses into a swarm snapshot.

Pure functions with no Home Assistant imports so they can be unit tested.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

NO_STACK = "standalone"
INGRESS_NETWORKS = {"ingress", "host", "bridge", "none"}

# Service states, worst first. The stack and swarm roll-ups take the worst one.
SERVICE_STATES = ("down", "degraded", "update_paused", "updating", "stopped", "running")
STACK_STATES = ("down", "degraded", "update_paused", "updating", "stopped", "running")

# Portainer image status values that mean a newer image is available.
OUTDATED = "outdated"

FAILED_TASK_STATES = {"failed", "rejected"}
FAILURE_WINDOW = timedelta(hours=24)
TOP_CONTAINERS = 5


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


def node_name(container: dict[str, Any], default: str | None = None) -> str:
    """Return the node (Pi) a container runs on, as reported by the Portainer agent.

    Environments Portainer reaches without its agent don't tag containers with a
    node, so ``default`` (the host's name) is used for those.
    """
    agent = (container.get("Portainer") or {}).get("Agent") or {}
    return str(agent.get("NodeName") or default or "unknown")


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


def cpu_sample(stats: dict[str, Any] | None) -> tuple[int, int, int] | None:
    """Return (container CPU ns, host CPU ns, online CPUs) from a stats sample."""
    if not stats:
        return None
    cpu = stats.get("cpu_stats") or {}
    total = (cpu.get("cpu_usage") or {}).get("total_usage")
    system = cpu.get("system_cpu_usage")
    if total is None or system is None:
        return None
    online = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    return int(total), int(system), int(online)


def cpu_percent(
    previous: tuple[int, int, int] | None, current: tuple[int, int, int] | None
) -> tuple[float | None, float | None]:
    """Return (docker-style CPU %, share of the whole node %) between two samples.

    One-shot stats carry no previous sample, so CPU use is measured between
    consecutive polls. Docker-style means 100% is one full core.
    """
    if previous is None or current is None:
        return None, None
    d_total = current[0] - previous[0]
    d_system = current[1] - previous[1]
    if d_total < 0 or d_system <= 0:
        return None, None
    node_share = d_total / d_system * 100
    return round(node_share * current[2], 1), round(node_share, 2)


def io_from_stats(stats: dict[str, Any] | None) -> dict[str, int | None]:
    """Return cumulative network and block I/O byte counters."""
    if not stats:
        return {"net_rx": None, "net_tx": None, "disk_read": None, "disk_write": None}
    networks = stats.get("networks") or {}
    blkio = (stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []
    return {
        "net_rx": sum(int(n.get("rx_bytes", 0)) for n in networks.values()) if networks else None,
        "net_tx": sum(int(n.get("tx_bytes", 0)) for n in networks.values()) if networks else None,
        "disk_read": sum(int(b.get("value", 0)) for b in blkio if str(b.get("op", "")).lower() == "read"),
        "disk_write": sum(int(b.get("value", 0)) for b in blkio if str(b.get("op", "")).lower() == "write"),
    }


def parse_time(value: Any) -> datetime | None:
    """Parse a Docker RFC 3339 timestamp (nanosecond precision) into UTC."""
    if not value or not isinstance(value, str) or value.startswith("0001-"):
        return None
    text = value.rstrip("Z")
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def node_info(node: dict[str, Any]) -> dict[str, Any]:
    """Summarise a swarm node object."""
    desc = node.get("Description") or {}
    spec = node.get("Spec") or {}
    status = node.get("Status") or {}
    manager = node.get("ManagerStatus") or {}
    resources = desc.get("Resources") or {}
    address = status.get("Addr")
    if not address or address == "0.0.0.0":
        address = str(manager.get("Addr", "")).rsplit(":", 1)[0] or address
    return {
        "id": node.get("ID", ""),
        "hostname": desc.get("Hostname") or node.get("ID", "unknown")[:12],
        "role": spec.get("Role", "unknown"),
        "availability": spec.get("Availability", "unknown"),
        "state": status.get("State", "unknown"),
        "leader": bool(manager.get("Leader")),
        "reachability": manager.get("Reachability"),
        "address": address,
        "engine_version": (desc.get("Engine") or {}).get("EngineVersion"),
        "os": (desc.get("Platform") or {}).get("OS"),
        "architecture": (desc.get("Platform") or {}).get("Architecture"),
        "cpus": round((resources.get("NanoCPUs") or 0) / 1e9, 2) or None,
        "memory_total_mib": round((resources.get("MemoryBytes") or 0) / 1048576, 1) or None,
    }


def host_info(name: str, info: dict[str, Any] | None) -> dict[str, Any]:
    """Summarise `docker info` for a host that isn't in a swarm, like node_info.

    There is no role, so the host doesn't count as a swarm node.
    """
    info = info or {}
    return {
        "hostname": name,
        "state": "ready" if info else "unknown",
        "engine_version": info.get("ServerVersion"),
        "os": info.get("OperatingSystem") or info.get("OSType"),
        "architecture": info.get("Architecture"),
        "cpus": info.get("NCPU"),
        "memory_total_mib": round((info.get("MemTotal") or 0) / 1048576, 1) or None,
    }


def compose_stack_state(running: int, total: int) -> str:
    """Return a STACK_STATES value for a compose project from its containers."""
    if running == 0:
        return "stopped"
    if running < total:
        return "degraded"
    return "running"


def service_state(running: int, desired: int, update_state: str | None) -> str:
    """Return one of SERVICE_STATES."""
    if desired == 0:
        return "stopped"
    if running == 0:
        return "down"
    if running < desired:
        return "degraded"
    if update_state == "paused" or update_state == "rollback_paused":
        return "update_paused"
    if update_state in ("updating", "rollback_started"):
        return "updating"
    return "running"


def worst(states: list[str], order: tuple[str, ...]) -> str | None:
    """Return the worst state in ``states`` according to ``order``."""
    present = [s for s in order if s in states]
    return present[0] if present else None


def _container_row(
    container: dict[str, Any],
    stats: dict[str, Any] | None,
    cpu: tuple[float | None, float | None],
    compose: bool = False,
) -> dict[str, Any]:
    labels = container.get("Labels") or {}
    mem_mib, mem_pct = memory_from_stats(stats)
    ip, networks = addresses(container)
    stack = labels.get("com.docker.stack.namespace")
    if not stack and compose:
        stack = labels.get("com.docker.compose.project")
    return {
        "name": container_name(container),
        "stack": stack or NO_STACK,
        "service": labels.get("com.docker.swarm.service.name"),
        "image": short_image(container.get("Image", "")),
        "health": health_state(container),
        "state": container.get("State", "unknown"),
        "uptime": uptime_text(container),
        "memory_mib": mem_mib,
        "memory_percent": mem_pct,
        "cpu_percent": cpu[0],
        "cpu_node_percent": cpu[1],
        "ip": ip,
        "networks": networks,
        "id": str(container.get("Id", ""))[:12],
        **io_from_stats(stats),
    }


def _sum(values: list[float | None]) -> float | None:
    real = [v for v in values if v is not None]
    return round(sum(real), 1) if real else None


def compose_stacks(
    standalone: dict[str, dict[str, Any]], portainer_by_name: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Build stack rows from compose projects, in the same shape as swarm stacks.

    Each stack's "services" are its containers. A Portainer stack with no containers
    (stopped, or never deployed) is listed as stopped.
    """
    members: dict[str, list[dict[str, Any]]] = {name: [] for name in portainer_by_name}
    for row in standalone.values():
        if row["stack"] != NO_STACK:
            members.setdefault(row["stack"], []).append(row)
    stacks: dict[str, dict[str, Any]] = {}
    for name, rows in members.items():
        rows = sorted(rows, key=lambda r: r["name"])
        running = [r for r in rows if r["state"] == "running"]
        portainer = portainer_by_name.get(name)
        updated = None
        if portainer and portainer.get("UpdateDate"):
            updated = datetime.fromtimestamp(int(portainer["UpdateDate"]), timezone.utc)
        stacks[name] = {
            "name": name,
            "state": compose_stack_state(len(running), len(rows)),
            "services": [r["name"] for r in rows],
            "services_running": len(running),
            "containers": len(running),
            "desired": len(rows),
            "nodes": sorted({r["node"] for r in rows}),
            "updates": sorted(r["name"] for r in rows if r.get("update") == OUTDATED),
            "unhealthy": sum(r["health"] == "unhealthy" for r in running),
            "failures_24h": 0,
            "cpu_percent": _sum([r["cpu_percent"] for r in running]),
            "memory_mib": _sum([r["memory_mib"] for r in running]),
            "portainer_id": portainer.get("Id") if portainer else None,
            "managed": portainer is not None,
            "git": bool(portainer and portainer.get("GitConfig")),
            "updated": _iso(updated),
        }
    return stacks


def build_snapshot(
    *,
    containers: list[dict[str, Any]],
    stats_by_id: dict[str, dict[str, Any] | None],
    previous_cpu: dict[str, tuple[int, int, int]],
    nodes: list[dict[str, Any]] | None = None,
    services: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    portainer_stacks: list[dict[str, Any]] | None = None,
    resource_counts: dict[str, dict[str, int]] | None = None,
    image_status: dict[str, str] | None = None,
    image_checked: datetime | None = None,
    now: datetime | None = None,
    swarm: bool = True,
    host_name: str | None = None,
    host: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, tuple[int, int, int]]]:
    """Build the whole swarm snapshot.

    Returns (snapshot, cpu samples to keep for the next poll). ``snapshot["nodes"]``
    keeps the original per-node table shape so existing dashboards continue to
    work; the rest of the snapshot is swarm-wide.

    With ``swarm=False`` the environment is a single Docker host (``host_name``, with
    ``host`` from `docker info`): it becomes the one node, every container is a
    standalone container, and stacks are the compose projects.
    """
    now = now or datetime.now(timezone.utc)
    nodes = nodes or []
    services = services or []
    tasks = tasks or []
    portainer_stacks = portainer_stacks or []
    resource_counts = resource_counts or {}
    image_status = image_status or {}
    default_node = None if swarm else host_name

    node_by_id = {n.get("ID"): node_info(n) for n in nodes}
    node_by_name = {info["hostname"]: info for info in node_by_id.values()}
    if default_node:
        node_by_name[default_node] = host_info(default_node, host)

    # Containers: one row each, with CPU measured against the previous poll.
    new_cpu: dict[str, tuple[int, int, int]] = {}
    rows_by_node: dict[str, list[dict[str, Any]]] = {}
    row_by_task: dict[str, dict[str, Any]] = {}
    standalone: dict[str, dict[str, Any]] = {}
    for container in containers:
        labels = container.get("Labels") or {}
        task_id = labels.get("com.docker.swarm.task.id")
        if task_id and container.get("State") != "running":
            # Exited containers of replaced swarm tasks (Docker keeps a few per slot as
            # task history). The tasks themselves are counted as failures if they failed.
            continue
        cid = str(container.get("Id", ""))
        stats = stats_by_id.get(cid)
        sample = cpu_sample(stats)
        if sample is not None:
            new_cpu[cid] = sample
        row = _container_row(
            container, stats, cpu_percent(previous_cpu.get(cid), sample), compose=not swarm
        )
        node = node_name(container, default_node)
        row["node"] = node
        rows_by_node.setdefault(node, []).append(row)
        if task_id:
            row_by_task[task_id] = row
        else:
            key = f"{node}/{row['name']}"
            row["update"] = image_status.get(f"container:{cid}")
            row["full_id"] = cid
            standalone[key] = {**row, "key": key}

    # Per-node tables (original shape) plus node details.
    node_tables: dict[str, Any] = {}
    for name in set(rows_by_node) | set(node_by_name):
        rows = rows_by_node.get(name, [])
        rows.sort(key=lambda r: (r["stack"] == NO_STACK, r["stack"], r["name"]))
        info = node_by_name.get(name, {"hostname": name})
        memory = round(sum(r["memory_mib"] or 0 for r in rows), 1)
        cpu_values = [r["cpu_node_percent"] for r in rows if r["state"] == "running"]
        node_cpu = round(sum(v for v in cpu_values if v is not None), 1) if any(
            v is not None for v in cpu_values
        ) else None
        total_mem = info.get("memory_total_mib")
        running_rows = [r for r in rows if r["state"] == "running"]
        top = sorted(
            (r for r in running_rows if r["cpu_percent"] is not None),
            key=lambda r: r["cpu_percent"],
            reverse=True,
        )[:TOP_CONTAINERS]
        node_tables[name] = {
            "containers": rows,
            "total": len(rows),
            "running": len(running_rows),
            "stopped": len(rows) - len(running_rows),
            # A stopped container keeps its last health status, so only running ones count.
            "unhealthy": sum(r["health"] == "unhealthy" for r in running_rows),
            "memory_mib": memory,
            "memory_percent": round(memory / total_mem * 100, 1) if total_mem else None,
            "cpu_percent": node_cpu,
            "top": [
                {"name": r["name"], "cpu_percent": r["cpu_percent"], "memory_mib": r["memory_mib"]}
                for r in top
            ],
            "info": info,
            "counts": resource_counts.get(name, {}),
            "tasks": 0,
        }

    # Services from their tasks.
    service_rows: dict[str, dict[str, Any]] = {}
    tasks_by_service: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        tasks_by_service.setdefault(task.get("ServiceID", ""), []).append(task)

    for service in services:
        spec = service.get("Spec") or {}
        name = spec.get("Name") or service.get("ID", "")[:12]
        labels = spec.get("Labels") or {}
        mode = spec.get("Mode") or {}
        status = service.get("ServiceStatus") or {}
        sid = service.get("ID", "")
        own_tasks = tasks_by_service.get(sid, [])
        live = [
            t for t in own_tasks
            if t.get("DesiredState") == "running" and (t.get("Status") or {}).get("State") == "running"
        ]
        if "Global" in mode:
            mode_name = "global"
            desired = int(status.get("DesiredTasks", len({t.get("NodeID") for t in own_tasks if t.get("DesiredState") == "running"})))
        else:
            mode_name = "replicated"
            desired = int(((mode.get("Replicated") or {}).get("Replicas")) or status.get("DesiredTasks") or 0)
        running = int(status.get("RunningTasks", len(live)))
        update = service.get("UpdateStatus") or {}
        update_state = update.get("State")
        state = service_state(running, desired, update_state)
        failures = []
        for task in own_tasks:
            task_status = task.get("Status") or {}
            if task_status.get("State") in FAILED_TASK_STATES:
                when = parse_time(task_status.get("Timestamp"))
                if when and now - when <= FAILURE_WINDOW:
                    failures.append(
                        {
                            "node": node_by_id.get(task.get("NodeID"), {}).get("hostname"),
                            "state": task_status.get("State"),
                            "error": task_status.get("Err") or task_status.get("Message"),
                            "when": _iso(when),
                        }
                    )
        failures.sort(key=lambda f: f["when"] or "", reverse=True)
        task_rows = []
        for task in sorted(live, key=lambda t: t.get("Slot") or 0):
            node = node_by_id.get(task.get("NodeID"), {}).get("hostname", "unknown")
            row = row_by_task.get(task.get("ID", ""), {})
            task_rows.append(
                {
                    "slot": task.get("Slot"),
                    "node": node,
                    "since": _iso(parse_time((task.get("Status") or {}).get("Timestamp"))),
                    "health": row.get("health"),
                    "cpu_percent": row.get("cpu_percent"),
                    "memory_mib": row.get("memory_mib"),
                    "ip": row.get("ip"),
                    "container": row.get("id"),
                }
            )
            if node in node_tables:
                node_tables[node]["tasks"] += 1
        container_spec = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
        ports = [
            f"{p.get('PublishedPort')}:{p.get('TargetPort')}/{p.get('Protocol', 'tcp')}"
            for p in ((service.get("Endpoint") or {}).get("Ports") or [])
            if p.get("PublishedPort")
        ]
        service_rows[name] = {
            "id": sid,
            "name": name,
            "stack": labels.get("com.docker.stack.namespace") or NO_STACK,
            "image": short_image(container_spec.get("Image", "")),
            "mode": mode_name,
            "desired": desired,
            "running": running,
            "state": state,
            "update_state": update_state,
            "update_message": update.get("Message"),
            "nodes": sorted({t["node"] for t in task_rows}),
            "tasks": task_rows,
            "unhealthy": sum(t["health"] == "unhealthy" for t in task_rows),
            "cpu_percent": _sum([t["cpu_percent"] for t in task_rows]),
            "memory_mib": _sum([t["memory_mib"] for t in task_rows]),
            "constraints": ((spec.get("TaskTemplate") or {}).get("Placement") or {}).get("Constraints") or [],
            "ports": ports,
            "failures_24h": len(failures),
            "last_failure": failures[0] if failures else None,
            "created": _iso(parse_time(service.get("CreatedAt"))),
            "updated": _iso(parse_time(service.get("UpdatedAt"))),
            "update": image_status.get(f"service:{sid}"),
        }

    # Stacks: every stack namespace with services, plus Portainer stacks with none.
    portainer_by_name = {str(s.get("Name")): s for s in portainer_stacks if s.get("Name")}
    stacks: dict[str, dict[str, Any]] = {}
    for svc in service_rows.values():
        if svc["stack"] == NO_STACK:
            continue
        stacks.setdefault(svc["stack"], {"services": []})["services"].append(svc["name"])
    for name in portainer_by_name:
        stacks.setdefault(name, {"services": []})
    for name, stack in stacks.items():
        members = [service_rows[s] for s in sorted(stack["services"])]
        portainer = portainer_by_name.get(name)
        updated = None
        if portainer and portainer.get("UpdateDate"):
            updated = datetime.fromtimestamp(int(portainer["UpdateDate"]), timezone.utc)
        stack.update(
            {
                "name": name,
                "state": worst([m["state"] for m in members], STACK_STATES) or "stopped",
                "services": [m["name"] for m in members],
                "services_running": sum(m["state"] in ("running", "updating", "update_paused") for m in members),
                "containers": sum(m["running"] for m in members),
                "desired": sum(m["desired"] for m in members),
                "nodes": sorted({n for m in members for n in m["nodes"]}),
                "updates": sorted(m["name"] for m in members if m["update"] == OUTDATED),
                "unhealthy": sum(m["unhealthy"] for m in members),
                "failures_24h": sum(m["failures_24h"] for m in members),
                "cpu_percent": _sum([m["cpu_percent"] for m in members]),
                "memory_mib": _sum([m["memory_mib"] for m in members]),
                "portainer_id": portainer.get("Id") if portainer else None,
                "managed": portainer is not None,
                "git": bool(portainer and portainer.get("GitConfig")),
                "updated": _iso(updated),
            }
        )
    if not swarm:
        # A single host has no services: its stacks are its compose projects.
        stacks = compose_stacks(standalone, portainer_by_name)

    updates = [
        {"kind": "service", "name": s["name"], "stack": s["stack"], "image": s["image"], "nodes": s["nodes"]}
        for s in service_rows.values()
        if s["update"] == OUTDATED
    ] + [
        {"kind": "container", "name": c["name"], "stack": c["stack"], "image": c["image"], "nodes": [c["node"]]}
        for c in standalone.values()
        if c["update"] == OUTDATED
    ]
    updates.sort(key=lambda u: (u["stack"], u["name"]))

    node_infos = [t["info"] for t in node_tables.values() if t["info"].get("role")]
    problems = [s["name"] for s in service_rows.values() if s["state"] in ("down", "degraded", "update_paused")]
    snapshot = {
        "swarm": swarm,
        "host": default_node,
        "nodes": node_tables,
        "nodes_total": len(node_infos),
        "nodes_ready": sum(i.get("state") == "ready" for i in node_infos),
        "managers": sum(i.get("role") == "manager" for i in node_infos),
        "leader": next((i["hostname"] for i in node_infos if i.get("leader")), None),
        "services": service_rows,
        "services_total": len(service_rows),
        "services_running": sum(s["state"] in ("running", "updating") for s in service_rows.values()),
        "problem_services": sorted(problems),
        "stacks": stacks,
        "standalone": standalone,
        "updates": updates,
        "updates_checked": _iso(image_checked),
        "tasks_running": sum(s["running"] for s in service_rows.values()),
        "failures_24h": sum(s["failures_24h"] for s in service_rows.values()),
        "unhealthy": sum(t["unhealthy"] for t in node_tables.values()),
    }
    return snapshot, new_cpu


def build_tables(
    containers: list[dict[str, Any]],
    stats_by_id: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """Group containers by node (kept for callers that only need the tables)."""
    snapshot, _ = build_snapshot(containers=containers, stats_by_id=stats_by_id, previous_cpu={})
    return snapshot["nodes"]
