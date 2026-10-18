# Swarm Containers

A read-only Home Assistant integration for a Docker Swarm, using the Portainer API. It understands the swarm: nodes and their roles, stacks, services and their replicas, where each task runs, failed tasks, paused updates, and image updates. It also ships dashboard cards.

Home Assistant's built-in Portainer integration fails on swarm environments because container-level requests only reach one node. Tools like Dockhand see each node's containers but not the swarm services above them. This integration reads the swarm objects directly and names the target node on each stats request (`X-PortainerAgent-Target`), so CPU and memory work for containers on every node.

## What it creates

### Docker Swarm (one device)

| Entity | State | Attributes |
|---|---|---|
| `sensor.docker_swarm_nodes_ready` | Nodes in the `ready` state | `total`, `managers`, `leader`, `nodes` |
| `sensor.docker_swarm_services_running` | Services running as desired | `total`, `problems` |
| `sensor.docker_swarm_stacks` | Number of stacks | stack names by state |
| `sensor.docker_swarm_tasks_running` | Running swarm tasks | |
| `sensor.docker_swarm_image_updates` | Images with a newer version | `updates`, `last_checked` |
| `sensor.docker_swarm_task_failures_24h` | Tasks that failed or were rejected in the last 24 hours | per service |
| `binary_sensor.docker_swarm_problem` | On when a node is not ready, a service is down, degraded or has a paused update, or a container is unhealthy | `reasons` |
| `button.docker_swarm_check_for_image_updates` | Runs the image check now | |

### Each node

| Entity | State |
|---|---|
| `sensor.<node>_containers` | Running containers; the full table is in the `containers` attribute |
| `sensor.<node>_container_cpu` | CPU used by containers, as a share of the node (top containers in `top`) |
| `sensor.<node>_container_memory` / `_container_memory_usage` | Memory used by containers (MiB / % of the node) |
| `sensor.<node>_tasks` | Swarm tasks running on the node |
| `sensor.<node>_role` | `manager` or `worker`; leader, availability, address and engine version as attributes |
| `binary_sensor.<node>_node_ready` | Node is `ready` |
| `sensor.<node>_images` / `_volumes` / `_networks` | Diagnostic counts |

### Each stack

`sensor.stack_<name>_status` (`running`, `degraded`, `down`, `update_paused`, `updating`, `stopped`) with services, nodes and pending updates as attributes, plus containers, CPU, memory and `binary_sensor.stack_<name>_image_update`. Stacks that Portainer manages but that have no services show as `stopped`. Stacks deployed outside Portainer show `managed: false`.

### Each service

`sensor.service_<name>_status` (same states as stacks) with stack, image, mode, running/desired replicas, the nodes it runs on, its tasks, update state, placement constraints, ports and the last failure as attributes. Also replicas, CPU, memory, task failures (24h) and `binary_sensor.service_<name>_image_update`.

### Each standalone container

Containers on the nodes that are not part of a swarm service (for example, started with `docker run` or compose) get a device named `<node> <container>` with state, health, CPU, memory and image update. Turn this off in the options if you don't want them.

Devices for stacks, services and containers that disappear are removed automatically.

## Dashboard cards

The cards are bundled with the integration, which adds them to your dashboard resources itself (and updates the entry after an upgrade). There's nothing to install separately. Search for "Swarm" in the add-card dialog. Each card has a visual editor.

| Card | Shows |
|---|---|
| `custom:swarm-overview-card` | Swarm health, nodes ready, services, stacks, tasks, updates, failures, and the list of problems |
| `custom:swarm-node-card` | One node: role, leader, running/stopped/unhealthy containers, tasks, CPU and memory bars, images/volumes/networks, top containers (`mode: compact / standard / detailed`) |
| `custom:swarm-stacks-card` | Every stack with status, services running, containers, updates and failures; `show_services: true` lists each stack's services with their nodes |
| `custom:swarm-services-card` | Services with replicas, the nodes they run on, CPU and memory; filter by `stack` or `node`, `group_by: stack / node / none`, `sort_by: name / status / cpu / memory`, `include_standalone: true` adds standalone containers |
| `custom:swarm-updates-card` | Services and containers with a newer image, when it was last checked, and a Check button |

```yaml
type: custom:swarm-node-card
device_id: <swarm node device>
mode: detailed
```

Clicking any row opens the matching entity.

## Install

1. In HACS, add this repository as a custom repository (category: Integration) and download it.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Swarm Containers**.

You will be asked for:

- **Portainer URL**, for example `https://portainer.local:9443`
- **API token**, created in Portainer under My account → Access tokens
- **Verify SSL** (turn off for a self-signed certificate)
- **Update interval** (30 to 900 seconds, default 60)
- **Environment**, only asked if Portainer has more than one

### Options

| Option | Default |
|---|---|
| Update interval | 60 s |
| Check for image updates | on |
| Image check interval | 6 hours (minimum 1 hour). Each check asks the registry once per image |
| Create devices for standalone containers | on |

## How values are measured

- **CPU** is measured between two polls, so it shows after the second update. Service and container CPU are `docker stats` style (100% = one core). Node CPU is the share of the whole node used by its containers.
- **Memory** excludes the page cache, as `docker stats` does. Node memory is the sum of its containers against the node's total RAM.
- **Image updates** come from Portainer's registry check. Services and containers whose image is pinned or unreachable show as unknown.
- **Task failures** count tasks that ended `failed` or `rejected` in the last 24 hours, from the swarm's task history.

## Security

- The integration only performs HTTP `GET` requests. It cannot start, stop, scale or change anything. The **Check for image updates** button only reads.
- The cards only read entities Home Assistant already has.
- The token is stored in the Home Assistant config entry, the same as any other integration credential.
- A Portainer access token carries the permissions of the user who created it. Use a user with the least access that can still read the environment.

## Development

```bash
pip install pytest-homeassistant-custom-component
python -m pytest -q
```

The parsing logic in `parse.py` has no Home Assistant imports, so `tests/test_parse.py` runs with plain `pytest`. `tests/test_init.py` sets the integration up against a mocked Portainer and is skipped when the Home Assistant test package is missing.

## License

MIT
