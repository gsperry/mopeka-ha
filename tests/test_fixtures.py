"""End-to-end tests driven by recorded API responses.

Snapshots capture every entity's registry entry and state, so any change to the
entities shows up as a snapshot diff for review.
"""

from __future__ import annotations

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.mopeka_ha.api import MopekaApiClient
from custom_components.mopeka_ha.const import (
    BACKEND_LEGACY,
    BACKEND_RETAIL,
    CONF_BACKEND,
    DOMAIN,
)

from .conftest import EMAIL, PASSWORD, load_json_fixture
from .fake_cloud import FakeCloud, install_fake_cloud


@pytest.fixture
def cloud(aioclient_mock: AiohttpClientMocker) -> FakeCloud:
    """Return a fake cloud serving the recorded fixtures for both platforms."""
    cloud = install_fake_cloud(aioclient_mock)
    cloud.current.add_user(EMAIL, PASSWORD)
    cloud.cognito.add_user(EMAIL, PASSWORD)
    cloud.retail.devices = load_json_fixture("retail_sync.json")["devices"]
    shadows = load_json_fixture("legacy_data_shadow.json")
    for device in load_json_fixture("legacy_devices.json")["devices"]:
        items = shadows[device["address"]]["timeSeries"]["Items"]
        cloud.gateway.add_tank(device, items[0])
    return cloud


async def _setup(hass: HomeAssistant, backend: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_BACKEND: backend},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_retail_entities(
    hass: HomeAssistant,
    cloud: FakeCloud,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """A recorded Co-Pilot account produces the expected entities and states."""
    entry = await _setup(hass, BACKEND_RETAIL)
    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)

    # The values checked against the Co-Pilot app and the Bluetooth integration.
    assert hass.states.get("sensor.mopeka_0001_level").state == "384.4"
    assert hass.states.get("sensor.mopeka_0002_level").state == "454.2"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_legacy_entities(
    hass: HomeAssistant,
    cloud: FakeCloud,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """A Tank Check account produces the expected entities and states."""
    entry = await _setup(hass, BACKEND_LEGACY)
    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_retail_response_without_new_samples(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A recorded sync with no new readings lists devices but no tanks."""
    cloud.retail.devices = load_json_fixture("retail_sync_no_new_samples.json")[
        "devices"
    ]
    client = MopekaApiClient(async_get_clientsession(hass), EMAIL, PASSWORD)
    data = await client.get_account_data()
    assert set(data.devices) == {"00:00:00:00:00:01", "00:00:00:00:00:02"}
    assert data.tanks == {}
