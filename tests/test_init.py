"""Set the integration up against a mocked Portainer and check the entities."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import fixtures as fx  # noqa: E402

DOMAIN = "swarm_containers"
CLIENT = "custom_components.swarm_containers.coordinator.PortainerClient"


def has_device(hass: HomeAssistant, entry, ident: tuple[str, str]) -> bool:
    registry = dr.async_get(hass)
    return any(ident in d.identifiers for d in dr.async_entries_for_config_entry(registry, entry.entry_id))


@pytest.fixture
def portainer():
    """A mocked read-only Portainer client with the synthetic swarm."""
    client = MagicMock()
    client.data = {
        "containers": copy.deepcopy(fx.CONTAINERS),
        "services": copy.deepcopy(fx.SERVICES),
    }
    client.get_containers = AsyncMock(side_effect=lambda _e: client.data["containers"])
    client.get_services = AsyncMock(side_effect=lambda _e: client.data["services"])
    client.get_nodes = AsyncMock(return_value=fx.NODES)
    client.get_tasks = AsyncMock(return_value=fx.TASKS)
    client.get_stacks = AsyncMock(return_value=fx.STACKS)
    client.get_images = AsyncMock(return_value=[{"Portainer": {"Agent": {"NodeName": "pi1"}}}])
    client.get_volumes = AsyncMock(return_value=[])
    client.get_networks = AsyncMock(return_value=[])
    client.get_stats = AsyncMock(return_value=fx.stats(0, 0, 50))
    client.get_service_image_status = AsyncMock(side_effect=lambda _e, sid: "outdated" if sid == "s1" else "updated")
    client.get_container_image_status = AsyncMock(return_value="updated")
    with patch(CLIENT, return_value=client):
        yield client


@pytest.fixture
async def entry(hass: HomeAssistant, portainer) -> MockConfigEntry:
    """Set up the integration (frontend registration stubbed out)."""
    hass.config.components.update({"frontend", "http"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Swarm containers (Swarm)",
        unique_id="instance#1",
        data={"url": "https://portainer:9443", "api_token": "x", "verify_ssl": False, "endpoint_id": "1"},
        options={"scan_interval": 60},
    )
    config_entry.add_to_hass(hass)
    with patch("custom_components.swarm_containers.add_extra_js_url") as add_js:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    # No dashboard resource store in the test, so the cards load on every page instead.
    add_js.assert_called_once()
    return config_entry


class FakeResources:
    """Just enough of Home Assistant's dashboard resource collection."""

    def __init__(self, items):
        self.items = items

    async def async_get_info(self):
        return {"resources": len(self.items)}

    def async_items(self):
        return self.items

    async def async_create_item(self, data):
        self.items.append({"id": "new", "type": data["res_type"], "url": data["url"]})

    async def async_update_item(self, item_id, data):
        next(i for i in self.items if i["id"] == item_id)["url"] = data["url"]


async def test_entities(hass: HomeAssistant, entry) -> None:
    def state(entity_id):
        s = hass.states.get(entity_id)
        assert s is not None, entity_id
        return s

    assert state("sensor.docker_swarm_nodes_ready").state == "2"
    assert state("sensor.docker_swarm_nodes_ready").attributes["leader"] == "pi1"
    assert state("sensor.docker_swarm_services_running").state == "2"
    assert state("binary_sensor.docker_swarm_problem").state == "on"
    assert "Service tools_broken is down" in state("binary_sensor.docker_swarm_problem").attributes["reasons"]
    # Version 0.1 entity ids are kept.
    assert state("sensor.pi1_containers").state == "3"
    assert state("sensor.pi1_container_memory").state == "150.0"
    assert state("sensor.pi1_role").state == "manager"
    assert state("binary_sensor.pi1_node_ready").state == "on"
    assert state("sensor.stack_web_status").state == "update_paused"
    assert state("sensor.service_web_app_status").attributes["nodes"] == ["pi1", "pi2"]
    assert state("sensor.service_web_app_replicas").state == "2"
    assert state("sensor.service_tools_broken_task_failures_24h").state == "1"
    assert state("sensor.pi2_glances_state").state == "running"
    assert state("sensor.pi2_glances_health").state == "none"


async def test_image_check_runs_in_background(hass: HomeAssistant, entry, portainer) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)
    updates = hass.states.get("sensor.docker_swarm_image_updates")
    assert updates.state == "1"
    assert updates.attributes["updates"][0]["name"] == "web_app"
    assert hass.states.get("binary_sensor.service_web_app_image_update").state == "on"
    assert hass.states.get("binary_sensor.stack_web_image_update").state == "on"
    assert hass.states.get("binary_sensor.service_web_db_image_update").state == "off"
    calls = portainer.get_service_image_status.await_count
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.docker_swarm_check_for_image_updates"}, blocking=True
    )
    assert portainer.get_service_image_status.await_count == calls + len(fx.SERVICES)


async def test_removed_service_loses_its_device(hass: HomeAssistant, entry, portainer) -> None:
    ident = (DOMAIN, f"{entry.entry_id}_service_tools_broken")
    assert has_device(hass, entry, ident)
    portainer.data["services"] = [s for s in portainer.data["services"] if s["ID"] != "s4"]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert not has_device(hass, entry, ident)
    assert hass.states.get("sensor.service_tools_broken_status") is None
    # Nodes and the swarm device are never removed.
    assert has_device(hass, entry, (DOMAIN, f"{entry.entry_id}_pi1"))


async def test_empty_answer_keeps_devices(hass: HomeAssistant, entry, portainer) -> None:
    portainer.data["services"] = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert has_device(hass, entry, (DOMAIN, f"{entry.entry_id}_service_web_app"))


async def test_options_flow(hass: HomeAssistant, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 120, "image_check": False, "image_check_interval": 7200, "standalone_containers": False},
    )
    await hass.async_block_till_done()
    assert entry.options["image_check"] is False
    assert hass.states.get("sensor.docker_swarm_image_updates").attributes["enabled"] is False


async def test_device_hierarchy(hass: HomeAssistant, entry) -> None:
    registry = dr.async_get(hass)
    devices = {
        next(i[1] for i in d.identifiers if i[0] == DOMAIN): d
        for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
    }
    prefix = f"{entry.entry_id}_"
    swarm = devices[f"{prefix}swarm"]
    assert swarm.via_device_id is None
    assert devices[f"{prefix}pi1"].via_device_id == swarm.id
    assert devices[f"{prefix}stack_web"].via_device_id == swarm.id
    assert devices[f"{prefix}service_web_app"].via_device_id == devices[f"{prefix}stack_web"].id
    assert devices[f"{prefix}container_pi2/glances"].via_device_id == devices[f"{prefix}pi2"].id


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ([], ["/swarm_containers/swarm-cards.js?v={v}"]),
        ([{"id": "a", "type": "module", "url": "/swarm_containers/swarm-cards.js?v=0.0.1"}], ["/swarm_containers/swarm-cards.js?v={v}"]),
        (
            [{"id": "a", "type": "module", "url": "/hacsfiles/other.js"}],
            ["/hacsfiles/other.js", "/swarm_containers/swarm-cards.js?v={v}"],
        ),
    ],
)
async def test_cards_registered_as_dashboard_resource(hass: HomeAssistant, existing, expected) -> None:
    from custom_components.swarm_containers import _async_register_cards
    from custom_components.swarm_containers.const import VERSION

    resources = FakeResources(list(existing))
    hass.data["lovelace"] = MagicMock(resources=resources)
    with patch("custom_components.swarm_containers.add_extra_js_url") as add_js:
        await _async_register_cards(hass)
        await _async_register_cards(hass)  # a second start changes nothing
    add_js.assert_not_called()
    assert [i["url"] for i in resources.items] == [u.format(v=VERSION) for u in expected]


@pytest.fixture
def host_portainer():
    """A mocked Portainer whose environment is a single Docker host, not a swarm."""
    from custom_components.swarm_containers.client import PortainerResponseError

    def not_swarm(_endpoint):
        raise PortainerResponseError("Portainer returned HTTP 503", 503)

    client = MagicMock()
    client.data = {"containers": copy.deepcopy(fx.HOST_CONTAINERS)}
    client.get_containers = AsyncMock(side_effect=lambda _e: client.data["containers"])
    client.get_nodes = AsyncMock(side_effect=not_swarm)
    client.get_services = AsyncMock(side_effect=not_swarm)
    client.get_tasks = AsyncMock(side_effect=not_swarm)
    client.get_stacks = AsyncMock(return_value=fx.HOST_STACKS)
    client.get_endpoint = AsyncMock(return_value={"Id": 1, "Name": "NAS"})
    client.get_info = AsyncMock(return_value=fx.HOST_INFO)
    client.get_images = AsyncMock(return_value=[{"Id": "i1"}, {"Id": "i2"}])
    client.get_volumes = AsyncMock(return_value=[])
    client.get_networks = AsyncMock(return_value=[])
    client.get_stats = AsyncMock(return_value=fx.stats(0, 0, 40))

    def no_image_check(*_args):
        # Portainer Community Edition has no image_status route.
        raise PortainerResponseError("Portainer returned HTTP 404", 404)

    client.get_service_image_status = AsyncMock(side_effect=no_image_check)
    client.get_container_image_status = AsyncMock(side_effect=no_image_check)
    with patch(CLIENT, return_value=client):
        yield client


@pytest.fixture
async def host_entry(hass: HomeAssistant, host_portainer) -> MockConfigEntry:
    hass.config.components.update({"frontend", "http"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Swarm containers (NAS)",
        unique_id="instance#nas",
        data={"url": "https://portainer:9443", "api_token": "x", "verify_ssl": False, "endpoint_id": "1"},
        options={"scan_interval": 60},
    )
    config_entry.add_to_hass(hass)
    with patch("custom_components.swarm_containers.add_extra_js_url"):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry


async def test_single_host_entities(hass: HomeAssistant, host_entry) -> None:
    assert host_entry.state.value == "loaded"
    assert hass.states.get("sensor.docker_nas_stacks").state == "3"
    assert hass.states.get("binary_sensor.docker_nas_problem").state == "off"
    assert hass.states.get("sensor.nas_containers").state == "3"
    assert hass.states.get("sensor.nas_images").state == "2"
    assert hass.states.get("binary_sensor.nas_node_ready").state == "on"
    assert hass.states.get("sensor.stack_media_status").state == "degraded"
    assert hass.states.get("sensor.stack_parked_status").state == "stopped"
    assert hass.states.get("sensor.nas_sonarr_state").state == "running"
    assert hass.states.get("sensor.nas_radarr_state").state == "exited"
    # Swarm-only sensors are not created for a single host.
    for entity_id in (
        "sensor.docker_nas_nodes_ready",
        "sensor.docker_nas_services_running",
        "sensor.docker_nas_tasks_running",
        "sensor.docker_nas_task_failures_24h",
        "sensor.nas_role",
        "sensor.nas_tasks",
    ):
        assert hass.states.get(entity_id) is None, entity_id


async def test_single_host_devices(hass: HomeAssistant, host_entry, host_portainer) -> None:
    registry = dr.async_get(hass)
    devices = {
        next(i[1] for i in d.identifiers if i[0] == DOMAIN): d
        for d in dr.async_entries_for_config_entry(registry, host_entry.entry_id)
    }
    prefix = f"{host_entry.entry_id}_"
    top = devices[f"{prefix}swarm"]
    assert top.name == "Docker NAS"
    assert top.configuration_url.endswith("/#!/1/docker/dashboard")
    assert devices[f"{prefix}NAS"].via_device_id == top.id
    assert devices[f"{prefix}container_NAS/sonarr"].via_device_id == devices[f"{prefix}NAS"].id
    assert "type=2" in devices[f"{prefix}stack_media"].configuration_url
    # The environment name is looked up once, not on every poll.
    await host_entry.runtime_data.async_refresh()
    assert host_portainer.get_endpoint.await_count == 1
    # A container that goes away loses its device.
    host_portainer.data["containers"] = [
        c for c in host_portainer.data["containers"] if c["Names"] != ["/watchtower"]
    ]
    await host_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert not has_device(hass, host_entry, (DOMAIN, f"{prefix}container_NAS/watchtower"))


async def test_other_swarm_errors_still_fail(hass: HomeAssistant, entry, portainer) -> None:
    from custom_components.swarm_containers.client import PortainerResponseError

    portainer.get_nodes.side_effect = PortainerResponseError("Portainer returned HTTP 500", 500)
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success is False


async def test_no_image_check_on_community_edition(hass: HomeAssistant, host_entry, host_portainer) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)
    updates = hass.states.get("sensor.docker_nas_image_updates")
    # Unknown, not "0 updates", and flagged as unsupported.
    assert updates.state == "unknown"
    assert updates.attributes["supported"] is False
    assert hass.states.get("binary_sensor.nas_sonarr_image_update").state == "unknown"
    calls = host_portainer.get_container_image_status.await_count
    assert calls == len(fx.HOST_CONTAINERS)
    # Later polls don't keep asking.
    await host_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert host_portainer.get_container_image_status.await_count == calls
