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
