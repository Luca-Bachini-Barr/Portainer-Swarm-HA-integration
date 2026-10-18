"""Tests for the pure parsing helpers (no Home Assistant needed)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "parse", Path(__file__).parents[1] / "custom_components" / "swarm_containers" / "parse.py"
)
parse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parse)

MIB = 1048576


def swarm(cid="a" * 64, node="node1", health="healthy", status="Up 2 hours (healthy)"):
    return {
        "Id": cid,
        "Names": ["/stack_web.1.abc"],
        "Image": "nginx:1.27@sha256:deadbeef",
        "State": "running",
        "Status": status,
        "Health": {"Status": health} if health else {},
        "Labels": {
            "com.docker.stack.namespace": "stack",
            "com.docker.swarm.service.name": "stack_web",
            "com.docker.swarm.task.name": "stack_web.1.abc",
        },
        "NetworkSettings": {
            "Networks": {
                "ingress": {"IPAddress": "10.0.0.5"},
                "stack_default": {"IPAddress": "10.0.1.7"},
            }
        },
        "Portainer": {"Agent": {"NodeName": node}},
    }


def test_swarm_row_fields():
    tables = parse.build_tables([swarm()], {})
    row = tables["node1"]["containers"][0]
    assert row["name"] == "stack_web"
    assert row["stack"] == "stack"
    assert row["image"] == "nginx:1.27"
    assert row["health"] == "healthy"
    assert row["uptime"] == "2 hours"
    assert row["ip"] == "10.0.1.7"  # stack network preferred over ingress
    assert row["networks"] == {"ingress": "10.0.0.5", "stack_default": "10.0.1.7"}
    assert row["memory_mib"] is None


def test_standalone_container():
    c = swarm()
    c["Labels"] = {}
    c["Names"] = ["/glances_pi1"]
    c["Health"] = {}
    c["Status"] = "Up 6 days"
    row = parse.build_tables([c], {})["node1"]["containers"][0]
    assert row["name"] == "glances_pi1"
    assert row["stack"] == "standalone"
    assert row["health"] == "none"
    assert row["uptime"] == "6 days"


def test_grouping_by_node_and_totals():
    containers = [swarm("a" * 64, "node1"), swarm("b" * 64, "node2", "unhealthy", "Up 1 hour (unhealthy)")]
    tables = parse.build_tables(containers, {})
    assert set(tables) == {"node1", "node2"}
    assert tables["node2"]["unhealthy"] == 1
    assert tables["node1"]["unhealthy"] == 0


def test_memory_matches_docker_stats():
    stats = {"memory_stats": {"usage": 300 * MIB, "limit": 1000 * MIB, "stats": {"inactive_file": 100 * MIB}}}
    used, pct = parse.memory_from_stats(stats)
    assert used == 200.0
    assert pct == 20.0


def test_memory_handles_missing_stats():
    assert parse.memory_from_stats(None) == (None, None)
    assert parse.memory_from_stats({}) == (None, None)
    used, pct = parse.memory_from_stats({"memory_stats": {"usage": 5 * MIB}})
    assert used == 5.0 and pct is None


def test_node_total_memory_sums_rows():
    a, b = swarm("a" * 64), swarm("b" * 64)
    stats = {
        "a" * 64: {"memory_stats": {"usage": 10 * MIB, "limit": MIB * 100, "stats": {}}},
        "b" * 64: {"memory_stats": {"usage": 30 * MIB, "limit": MIB * 100, "stats": {}}},
    }
    assert parse.build_tables([a, b], stats)["node1"]["memory_mib"] == 40.0


def test_health_falls_back_to_status_text():
    c = swarm(health=None, status="Up 5 minutes (health: starting)")
    assert parse.health_state(c) == "starting"


# ---------- swarm snapshot ----------

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import fixtures as fx  # noqa: E402


def snapshot(image_status=None, previous_cpu=None, stats=None):
    return parse.build_snapshot(
        containers=fx.CONTAINERS,
        stats_by_id=stats or {},
        previous_cpu=previous_cpu or {},
        nodes=fx.NODES,
        services=fx.SERVICES,
        tasks=fx.TASKS,
        portainer_stacks=[s for s in fx.STACKS if s["EndpointId"] == 1],
        resource_counts={"pi1": {"images": 3}},
        image_status=image_status,
        now=fx.NOW,
    )


def test_cpu_needs_two_samples():
    assert parse.cpu_percent(None, (1, 2, 4)) == (None, None)
    # 0.5 s of container CPU over 10 s of host CPU time (4 cores): 5% of the node, 20% of a core.
    docker_pct, node_pct = parse.cpu_percent((0, 0, 4), (500_000_000, 10_000_000_000, 4))
    assert node_pct == 5.0
    assert docker_pct == 20.0
    assert parse.cpu_percent((10, 10, 4), (5, 20, 4)) == (None, None)  # counter reset


def test_service_states():
    assert parse.service_state(2, 2, "completed") == "running"
    assert parse.service_state(1, 2, None) == "degraded"
    assert parse.service_state(0, 1, None) == "down"
    assert parse.service_state(0, 0, None) == "stopped"
    assert parse.service_state(1, 1, "paused") == "update_paused"
    assert parse.service_state(1, 1, "updating") == "updating"


def test_nodes_info_and_counts():
    snap, _ = snapshot()
    pi1 = snap["nodes"]["pi1"]
    assert pi1["info"]["leader"] is True
    assert pi1["info"]["address"] == "192.168.1.57"  # leader reports 0.0.0.0; use the manager address
    assert pi1["info"]["memory_total_mib"] == 8192
    assert pi1["tasks"] == 3
    assert pi1["counts"] == {"images": 3}
    assert snap["nodes"]["pi2"]["info"]["role"] == "worker"
    assert snap["nodes_ready"] == 2 and snap["managers"] == 1 and snap["leader"] == "pi1"


def test_services_replicas_placement_and_failures():
    snap, _ = snapshot()
    app = snap["services"]["web_app"]
    assert (app["running"], app["desired"], app["state"]) == (2, 2, "running")
    assert app["nodes"] == ["pi1", "pi2"]
    assert app["ports"] == ["8080:80/tcp"]
    assert snap["services"]["web_db"]["state"] == "update_paused"
    assert snap["services"]["web_db"]["unhealthy"] == 1
    agent = snap["services"]["tools_agent"]
    assert agent["mode"] == "global" and agent["nodes"] == ["pi1", "pi2"]
    broken = snap["services"]["tools_broken"]
    assert broken["state"] == "down"
    assert broken["failures_24h"] == 1  # the 3-day-old failure is outside the window
    assert broken["last_failure"]["error"] == "exit 1"
    assert snap["problem_services"] == ["tools_broken", "web_db"]


def test_stacks_roll_up():
    snap, _ = snapshot()
    web = snap["stacks"]["web"]
    assert web["state"] == "update_paused"
    assert web["containers"] == 3 and web["desired"] == 3
    assert web["managed"] is True and web["portainer_id"] == 3
    assert snap["stacks"]["tools"]["state"] == "down"
    assert snap["stacks"]["tools"]["managed"] is False
    assert snap["stacks"]["parked"]["state"] == "stopped"  # Portainer stack with no services
    assert "other_env" not in snap["stacks"]


def test_standalone_containers_keyed_by_node():
    snap, _ = snapshot()
    assert list(snap["standalone"]) == ["pi2/glances"]
    assert snap["standalone"]["pi2/glances"]["full_id"] == "c6" * 32


def test_updates_from_image_status():
    snap, _ = snapshot(image_status={"service:s1": "outdated", "service:s2": "updated", f"container:{'c6' * 32}": "outdated"})
    assert [(u["kind"], u["name"]) for u in snap["updates"]] == [("container", "glances"), ("service", "web_app")]
    assert snap["stacks"]["web"]["updates"] == ["web_app"]
    assert snap["services"]["web_db"]["update"] == "updated"


def test_cpu_and_memory_roll_up_between_polls():
    first = {cid: fx.stats(0, 0, 100) for cid in (c["Id"] for c in fx.CONTAINERS)}
    _, samples = snapshot(stats=first)
    second = {cid: fx.stats(1_000_000_000, 10_000_000_000, 100) for cid in first}
    snap, _ = snapshot(stats=second, previous_cpu=samples)
    app = snap["services"]["web_app"]
    assert app["cpu_percent"] == 80.0  # two tasks, each 40% of a core
    assert app["memory_mib"] == 200.0
    assert snap["nodes"]["pi1"]["cpu_percent"] == 30.0  # three containers at 10% of the node each
    assert snap["nodes"]["pi1"]["memory_percent"] == round(300 / 8192 * 100, 1)
    assert snap["nodes"]["pi1"]["top"][0]["cpu_percent"] == 40.0
    row = snap["nodes"]["pi1"]["containers"][0]
    assert (row["net_rx"], row["disk_write"]) == (10, 7)


def test_exited_task_containers_are_task_history():
    old = fx.container("d1" * 32, "web_app.1.old", "pi1", "t_old", "web_app", "web", health="unhealthy")
    old["State"] = "exited"
    old["Status"] = "Exited (137) 6 hours ago"
    snap, _ = parse.build_snapshot(
        containers=[*fx.CONTAINERS, old], stats_by_id={}, previous_cpu={}, nodes=fx.NODES,
        services=fx.SERVICES, tasks=fx.TASKS, now=fx.NOW,
    )
    pi1 = snap["nodes"]["pi1"]
    assert pi1["total"] == 3 and pi1["stopped"] == 0
    assert snap["unhealthy"] == 1  # only the running web_db container


def test_stopped_standalone_container_is_kept_but_not_unhealthy():
    stopped = fx.container("e1" * 32, "old_tool", "pi2", health="unhealthy")
    stopped["State"] = "exited"
    snap, _ = parse.build_snapshot(
        containers=[*fx.CONTAINERS, stopped], stats_by_id={}, previous_cpu={}, nodes=fx.NODES,
        services=fx.SERVICES, tasks=fx.TASKS, now=fx.NOW,
    )
    assert "pi2/old_tool" in snap["standalone"]
    assert snap["nodes"]["pi2"]["stopped"] == 1
    assert snap["nodes"]["pi2"]["unhealthy"] == 0
