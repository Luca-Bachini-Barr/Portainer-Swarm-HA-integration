# Swarm Containers

A read-only Home Assistant integration that lists every container in a Docker Swarm, grouped by the node (Raspberry Pi) it runs on, using the Portainer API.

For each container you get: **name, stack, image, health, memory use, uptime and stack IP address.**

It is built for Portainer swarm environments, where Home Assistant's built-in Portainer integration fails because container-level requests only reach one node. This integration names the target node on each stats request (`X-PortainerAgent-Target`), so memory works for containers on every node.

## What it creates

For each swarm node, a device with two sensors:

| Sensor | State | Attributes |
|---|---|---|
| `sensor.<node>_containers` | Number of running containers | `containers` (the full table), `total`, `unhealthy` |
| `sensor.<node>_container_memory` | Memory used by all containers on the node (MiB) | |

Each entry in the `containers` attribute has: `name`, `stack`, `image`, `health` (`healthy` / `unhealthy` / `starting` / `none`), `state`, `uptime`, `memory_mib`, `memory_percent`, `ip`, `networks`, `id`.

The table is kept out of the recorder database, so it does not bloat your history.

## Install

1. In HACS, add this repository as a custom repository (category: Integration) and download it.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Swarm Containers**.

You will be asked for:

- **Portainer URL**, for example `https://192.168.1.60:9061`
- **API token**, created in Portainer under My account → Access tokens
- **Verify SSL** (turn off for a self-signed certificate)
- **Update interval** (30 to 900 seconds, default 60)
- **Environment**, only asked if Portainer has more than one

## Show it on a dashboard

A markdown card renders the table:

```yaml
type: markdown
content: >
  {% set rows = state_attr('sensor.piserver1_containers', 'containers') or [] %}
  | Container | Stack | Image | Health | Memory | Uptime | IP |
  |---|---|---|---|---|---|---|
  {% for r in rows -%}
  | {{ r.name }} | {{ r.stack }} | {{ r.image }} | {{ r.health }} | {{ r.memory_mib }} MiB | {{ r.uptime }} | {{ r.ip }} |
  {% endfor %}
entity_id: sensor.piserver1_containers
```

## Security

- The integration only performs HTTP `GET` requests. It cannot start, stop or change anything.
- The token is stored in the Home Assistant config entry, the same as any other integration credential.
- A Portainer access token carries the permissions of the user who created it. Use a user with the least access that can still read the environment.

## Limits

- Memory is a point-in-time sample taken each update, as `docker stats` shows it (page cache excluded).
- Uptime is the text Docker reports (for example "3 hours"), not a number.
- A container with no health check shows health `none`.

## Development

```bash
python -m pytest -q
```

The parsing logic in `parse.py` has no Home Assistant imports, so it is tested on its own.

## License

MIT
