"""Small synthetic Portainer responses: two nodes, two stacks, one standalone container."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
MIB = 1048576


def ts(delta: timedelta) -> str:
    return (NOW - delta).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


NODES = [
    {
        "ID": "n1",
        "Description": {
            "Hostname": "pi1",
            "Resources": {"NanoCPUs": 4_000_000_000, "MemoryBytes": 8192 * MIB},
            "Engine": {"EngineVersion": "29.8.0"},
            "Platform": {"OS": "linux", "Architecture": "aarch64"},
        },
        "Spec": {"Role": "manager", "Availability": "active"},
        "Status": {"State": "ready", "Addr": "0.0.0.0"},
        "ManagerStatus": {"Leader": True, "Reachability": "reachable", "Addr": "192.168.1.57:2377"},
    },
    {
        "ID": "n2",
        "Description": {
            "Hostname": "pi2",
            "Resources": {"NanoCPUs": 4_000_000_000, "MemoryBytes": 8192 * MIB},
            "Engine": {"EngineVersion": "29.8.0"},
        },
        "Spec": {"Role": "worker", "Availability": "active"},
        "Status": {"State": "ready", "Addr": "192.168.1.58"},
    },
]


def service(sid, name, stack, replicas=1, running=1, mode="replicated", update_state="completed"):
    spec_mode = {"Global": {}} if mode == "global" else {"Replicated": {"Replicas": replicas}}
    return {
        "ID": sid,
        "CreatedAt": ts(timedelta(days=30)),
        "UpdatedAt": ts(timedelta(days=1)),
        "Spec": {
            "Name": name,
            "Labels": {"com.docker.stack.namespace": stack},
            "Mode": spec_mode,
            "TaskTemplate": {"ContainerSpec": {"Image": f"example/{name}:latest@sha256:abc"}},
        },
        "Endpoint": {"Ports": [{"PublishedPort": 8080, "TargetPort": 80, "Protocol": "tcp"}]} if sid == "s1" else {},
        "ServiceStatus": {"RunningTasks": running, "DesiredTasks": replicas},
        "UpdateStatus": {"State": update_state},
    }


SERVICES = [
    service("s1", "web_app", "web", replicas=2, running=2),
    service("s2", "web_db", "web", replicas=1, running=1, update_state="paused"),
    service("s3", "tools_agent", "tools", replicas=2, running=2, mode="global"),
    service("s4", "tools_broken", "tools", replicas=1, running=0),
]


def task(tid, sid, node, slot, state="running", desired="running", age=timedelta(hours=1), err=None):
    status = {"State": state, "Timestamp": ts(age)}
    if err:
        status["Err"] = err
    return {"ID": tid, "ServiceID": sid, "NodeID": node, "Slot": slot, "DesiredState": desired, "Status": status}


TASKS = [
    task("t1", "s1", "n1", 1),
    task("t2", "s1", "n2", 2),
    task("t3", "s2", "n1", 1),
    task("t4", "s3", "n1", None),
    task("t5", "s3", "n2", None),
    task("t6", "s4", "n2", 1, state="failed", desired="shutdown", age=timedelta(hours=2), err="exit 1"),
    task("t7", "s4", "n2", 1, state="failed", desired="shutdown", age=timedelta(days=3), err="old"),
]


def container(cid, name, node, task_id=None, service_name=None, stack=None, health="healthy"):
    labels = {}
    if task_id:
        labels = {
            "com.docker.swarm.task.id": task_id,
            "com.docker.swarm.service.name": service_name,
            "com.docker.swarm.task.name": f"{service_name}.1.{task_id}",
            "com.docker.stack.namespace": stack,
        }
    return {
        "Id": cid,
        "Names": [f"/{name}"],
        "Image": "example/image:latest",
        "State": "running",
        "Status": "Up 2 hours" + (" (healthy)" if health == "healthy" else ""),
        "Health": {"Status": health} if health else {},
        "Labels": labels,
        "NetworkSettings": {"Networks": {"web_default": {"IPAddress": "10.0.1.2"}}},
        "Portainer": {"Agent": {"NodeName": node}},
    }


CONTAINERS = [
    container("c1" * 32, "web_app.1.t1", "pi1", "t1", "web_app", "web"),
    container("c2" * 32, "web_app.2.t2", "pi2", "t2", "web_app", "web"),
    container("c3" * 32, "web_db.1.t3", "pi1", "t3", "web_db", "web", health="unhealthy"),
    container("c4" * 32, "tools_agent.n1", "pi1", "t4", "tools_agent", "tools"),
    container("c5" * 32, "tools_agent.n2", "pi2", "t5", "tools_agent", "tools"),
    container("c6" * 32, "glances", "pi2", health=None),
]

STACKS = [
    {"Id": 3, "Name": "web", "EndpointId": 1, "UpdateDate": 1792000000},
    {"Id": 9, "Name": "parked", "EndpointId": 1, "UpdateDate": 0},
    {"Id": 10, "Name": "other_env", "EndpointId": 2},
]


def stats(cpu_total, system, mem_used_mib):
    return {
        "cpu_stats": {"cpu_usage": {"total_usage": cpu_total}, "system_cpu_usage": system, "online_cpus": 4},
        "memory_stats": {"usage": mem_used_mib * MIB, "limit": 8192 * MIB, "stats": {"inactive_file": 0}},
        "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}},
        "blkio_stats": {"io_service_bytes_recursive": [{"op": "read", "value": 5}, {"op": "write", "value": 7}]},
    }
