"""Management actions and buttons against a mocked Portainer."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: E402
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import fixtures as fx  # noqa: E402

from custom_components.swarm_containers.client import (  # noqa: E402
    PortainerClient,
    PortainerResponseError,
)

DOMAIN = "swarm_containers"
CLIENT = "custom_components.swarm_containers.coordinator.PortainerClient"


@pytest.fixture
def portainer():
    client = MagicMock()
    services = copy.deepcopy(fx.SERVICES)
    client.get_containers = AsyncMock(return_value=copy.deepcopy(fx.CONTAINERS))
    client.get_services = AsyncMock(return_value=services)
    client.get_nodes = AsyncMock(return_value=fx.NODES)
    client.get_tasks = AsyncMock(return_value=fx.TASKS)
    client.get_stacks = AsyncMock(return_value=fx.STACKS)
    client.get_images = AsyncMock(return_value=[])
    client.get_volumes = AsyncMock(return_value=[])
    client.get_networks = AsyncMock(return_value=[])
    client.get_stats = AsyncMock(return_value=fx.stats(0, 0, 50))
    client.get_service_image_status = AsyncMock(return_value="updated")
    client.get_container_image_status = AsyncMock(return_value="updated")

    def get_service(_e, sid):
        svc = next(s for s in services if s["ID"] == sid)
        return {**copy.deepcopy(svc), "Version": {"Index": 41}, "PreviousSpec": {"Name": "old"}}

    client.get_service = AsyncMock(side_effect=get_service)
    client.update_service = AsyncMock()
    client.remove_service = AsyncMock()
    client.container_action = AsyncMock()
    client.get_stack = AsyncMock(return_value={"Id": 3, "Env": [{"name": "A", "value": "1"}]})
    client.get_stack_file = AsyncMock(return_value="services: {}\n")
    client.redeploy_stack = AsyncMock()
    client.stack_action = AsyncMock()
    client.get_node = AsyncMock(return_value={"Version": {"Index": 7}, "Spec": {"Role": "worker", "Availability": "active"}})
    client.update_node = AsyncMock()
    with patch(CLIENT, return_value=client):
        yield client


async def _setup(hass: HomeAssistant, management: bool) -> MockConfigEntry:
    hass.config.components.update({"frontend", "http"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Swarm containers (Swarm)",
        unique_id="instance#1",
        data={"url": "https://portainer:9443", "api_token": "x", "verify_ssl": False, "endpoint_id": "1"},
        options={"scan_interval": 60, "image_check": False, "management": management},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.swarm_containers.add_extra_js_url"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.fixture
async def entry(hass: HomeAssistant, portainer) -> MockConfigEntry:
    return await _setup(hass, True)


def device(hass: HomeAssistant, entry, suffix: str) -> str:
    ident = (DOMAIN, f"{entry.entry_id}_{suffix}")
    registry = dr.async_get(hass)
    found = [d for d in dr.async_entries_for_config_entry(registry, entry.entry_id) if ident in d.identifiers]
    assert found, suffix
    return found[0].id


async def call(hass: HomeAssistant, action: str, **data) -> None:
    await hass.services.async_call(DOMAIN, action, data, blocking=True)


async def test_buttons_only_with_management(hass: HomeAssistant, portainer) -> None:
    entry = await _setup(hass, False)
    assert hass.states.get("button.service_web_app_restart") is None
    assert hass.states.get("button.docker_swarm_check_for_image_updates") is not None

    hass.config_entries.async_update_entry(entry, options={**entry.options, "management": True})
    await hass.async_block_till_done()
    assert hass.states.get("button.service_web_app_restart") is not None
    assert hass.states.get("button.stack_web_restart") is not None
    assert hass.states.get("button.stack_web_redeploy_pull_images").state != "unavailable"
    # The tools stack wasn't deployed through Portainer, so it can't be redeployed.
    assert hass.states.get("button.stack_tools_redeploy_pull_images").state == "unavailable"
    assert hass.states.get("button.pi2_glances_restart") is not None

    # Turning management off again removes the buttons it made.
    hass.config_entries.async_update_entry(entry, options={**entry.options, "management": False})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get("button.service_web_app_restart") is None
    assert registry.async_get("button.docker_swarm_check_for_image_updates") is not None


async def test_actions_refused_without_management(hass: HomeAssistant, portainer) -> None:
    entry = await _setup(hass, False)
    with pytest.raises(ServiceValidationError, match="turned off"):
        await call(hass, "restart", device_id=device(hass, entry, "service_web_app"))
    portainer.update_service.assert_not_called()


async def test_restart_service_forces_update(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "restart", device_id=device(hass, entry, "service_web_app"))
    endpoint, sid, version, spec = portainer.update_service.call_args.args
    assert (endpoint, sid, version) == (1, "s1", 41)
    assert spec["TaskTemplate"]["ForceUpdate"] == 1
    # The rest of the spec goes back unchanged.
    assert spec["Mode"] == {"Replicated": {"Replicas": 2}}


async def test_restart_button(hass: HomeAssistant, entry, portainer) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.service_web_db_restart"}, blocking=True
    )
    assert portainer.update_service.call_args.args[1] == "s2"


async def test_restart_stack_restarts_each_service(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "restart", device_id=device(hass, entry, "stack_web"))
    assert sorted(c.args[1] for c in portainer.update_service.call_args_list) == ["s1", "s2"]


async def test_restart_container(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "restart", device_id=device(hass, entry, "container_pi2/glances"))
    portainer.container_action.assert_awaited_once_with(1, "c6" * 32, "restart", "pi2")


async def test_redeploy_from_service_uses_its_stack(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "redeploy_stack", device_id=device(hass, entry, "service_web_app"))
    portainer.redeploy_stack.assert_awaited_once_with(
        3, 1, "services: {}\n", [{"name": "A", "value": "1"}], True, False
    )


async def test_redeploy_external_stack_refused(hass: HomeAssistant, entry, portainer) -> None:
    with pytest.raises(ServiceValidationError, match="not deployed through Portainer"):
        await call(hass, "redeploy_stack", device_id=device(hass, entry, "stack_tools"))


async def test_stop_and_start_stack(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "stop", device_id=device(hass, entry, "stack_web"))
    await call(hass, "start", device_id=device(hass, entry, "stack_parked"))
    assert [c.args for c in portainer.stack_action.call_args_list] == [(3, 1, "stop"), (9, 1, "start")]


async def test_stop_refuses_a_service(hass: HomeAssistant, entry, portainer) -> None:
    with pytest.raises(ServiceValidationError, match="works on stack / container"):
        await call(hass, "stop", device_id=device(hass, entry, "service_web_app"))


async def test_scale(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "scale_service", device_id=device(hass, entry, "service_web_app"), replicas=0)
    assert portainer.update_service.call_args.args[3]["Mode"] == {"Replicated": {"Replicas": 0}}
    with pytest.raises(ServiceValidationError, match="global service"):
        await call(hass, "scale_service", device_id=device(hass, entry, "service_tools_agent"), replicas=3)


async def test_rollback(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "rollback_service", device_id=device(hass, entry, "service_web_app"))
    assert portainer.update_service.call_args.kwargs == {"rollback": True}


async def test_remove_and_recreate(hass: HomeAssistant, entry, portainer) -> None:
    with patch("custom_components.swarm_containers.manage.asyncio.sleep", AsyncMock()):
        await call(hass, "remove_service", device_id=device(hass, entry, "service_web_db"), recreate=True)
    portainer.remove_service.assert_awaited_once_with(1, "s2")
    assert portainer.redeploy_stack.call_args.args[4] is False  # no pull on recreate
    # Recreating needs a Portainer stack; nothing is removed when there isn't one.
    portainer.remove_service.reset_mock()
    with pytest.raises(ServiceValidationError):
        await call(hass, "remove_service", device_id=device(hass, entry, "service_tools_broken"), recreate=True)
    portainer.remove_service.assert_not_called()


async def test_portainer_itself_is_protected(hass: HomeAssistant, entry, portainer) -> None:
    # Make web_db look like the Portainer agent (the snapshot is rebuilt after each action).
    db = next(s for s in portainer.get_services.return_value if s["ID"] == "s2")
    db["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"] = "portainer/agent:2.45.3"
    await entry.runtime_data.async_refresh()
    with pytest.raises(ServiceValidationError, match="Portainer itself"):
        await call(hass, "remove_service", device_id=device(hass, entry, "service_web_db"))
    with pytest.raises(ServiceValidationError, match="Portainer itself"):
        await call(hass, "stop", device_id=device(hass, entry, "stack_web"))
    # Restarting it is fine.
    await call(hass, "restart", device_id=device(hass, entry, "service_web_db"))


async def test_node_availability(hass: HomeAssistant, entry, portainer) -> None:
    await call(hass, "set_node_availability", device_id=device(hass, entry, "pi2"), availability="drain")
    endpoint, node_id, version, spec = portainer.update_node.call_args.args
    assert (endpoint, node_id, version, spec["Availability"]) == (1, "n2", 7, "drain")


async def test_portainer_error_is_reported(hass: HomeAssistant, entry, portainer) -> None:
    portainer.update_service.side_effect = PortainerResponseError("Portainer returned HTTP 500: update out of sequence", 500)
    with pytest.raises(HomeAssistantError, match="out of sequence"):
        await call(hass, "restart", device_id=device(hass, entry, "service_web_app"))


async def test_client_write_requests(hass: HomeAssistant, aioclient_mock) -> None:
    base = "https://portainer:9443"
    aioclient_mock.post(f"{base}/api/endpoints/1/docker/services/s1/update", json={"Warnings": None})
    aioclient_mock.post(f"{base}/api/endpoints/1/docker/containers/abc/start", status=304)
    aioclient_mock.delete(f"{base}/api/endpoints/1/docker/services/s2", status=200)
    aioclient_mock.put(f"{base}/api/stacks/3", status=400, json={"message": "Unable to deploy", "details": "bad file"})
    client = PortainerClient(async_get_clientsession(hass), base, "token")

    await client.update_service(1, "s1", 41, {"Name": "web_app"})
    method, url, body, headers = aioclient_mock.mock_calls[-1]
    assert method == "POST"
    assert url.query["version"] == "41" and url.query["registryAuthFrom"] == "spec"
    assert body == {"Name": "web_app"} and headers["X-API-Key"] == "token"

    await client.container_action(1, "abc", "start", "pi1")  # 304: already running
    assert aioclient_mock.mock_calls[-1][3]["X-PortainerAgent-Target"] == "pi1"
    await client.remove_service(1, "s2")
    with pytest.raises(PortainerResponseError, match="Unable to deploy: bad file"):
        await client.redeploy_stack(3, 1, "x", [], True, False)


async def test_restart_respects_shared_networks(hass: HomeAssistant, portainer) -> None:
    """A VPN container's dependents restart after it, whether it or its stack is restarted."""
    from custom_components.swarm_containers.client import PortainerResponseError as Err

    def not_swarm(_endpoint):
        raise Err("Portainer returned HTTP 503", 503)

    vpn = fx.host_container("v1" * 32, "gluetun", "vpn")
    app = fx.host_container("v2" * 32, "qbittorrent", "vpn")
    app["HostConfig"] = {"NetworkMode": f"container:{vpn['Id']}"}
    by_name = fx.host_container("v3" * 32, "other", "misc")
    by_name["HostConfig"] = {"NetworkMode": "container:gluetun"}
    portainer.get_containers = AsyncMock(return_value=[vpn, app, by_name])
    for name in ("get_nodes", "get_services", "get_tasks"):
        setattr(portainer, name, AsyncMock(side_effect=not_swarm))
    portainer.get_stacks = AsyncMock(return_value=[{"Id": 5, "Name": "vpn", "EndpointId": 1}])
    portainer.get_endpoint = AsyncMock(return_value={"Id": 1, "Name": "NAS"})
    portainer.get_info = AsyncMock(return_value=fx.HOST_INFO)
    entry = await _setup(hass, True)
    order = []
    portainer.container_action = AsyncMock(side_effect=lambda _e, cid, _a, _n: order.append(cid[:2]))

    await call(hass, "restart", device_id=device(hass, entry, "container_NAS/gluetun"))
    assert order[0] == "v1" and sorted(order[1:]) == ["v2", "v3"]

    order.clear()
    await call(hass, "restart", device_id=device(hass, entry, "stack_vpn"))
    assert order[0] == "v1" and sorted(order[1:]) == ["v2", "v3"]

    # A dependent on its own restarts alone.
    order.clear()
    await call(hass, "restart", device_id=device(hass, entry, "container_NAS/qbittorrent"))
    assert order == ["v2"]
