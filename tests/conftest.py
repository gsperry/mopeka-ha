"""Shared fixtures for Mopeka HA tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import (
    HomeAssistantSnapshotExtension,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.mopeka_ha.api import MopekaApiClient
from custom_components.mopeka_ha.const import CONF_UPDATE_INTERVAL, DOMAIN

from .fake_cloud import FakeCloud, install_fake_cloud, make_device, make_reading

EMAIL = "user@example.com"
PASSWORD = "correct-horse-battery"
ADDRESS_A = "AA:BB:CC:00:00:01"
ADDRESS_B = "AA:BB:CC:00:00:02"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""
    return


@pytest.fixture
def fake_cloud(aioclient_mock: AiohttpClientMocker) -> FakeCloud:
    """Return a fake Mopeka Cloud with one user and two tanks with readings."""
    cloud = install_fake_cloud(aioclient_mock)
    cloud.cognito.add_user(EMAIL, PASSWORD)
    cloud.gateway.add_tank(make_device(ADDRESS_A, "Tank A"), make_reading())
    cloud.gateway.add_tank(
        make_device(
            ADDRESS_B, "Tank B", tank_type="100gal", tank_height=0.9, vertical=False
        ),
        make_reading(level_m=0.467, temp_c=8, battery=2.71, source="gw-0002"),
    )
    return cloud


@pytest.fixture
def api_client(hass: HomeAssistant, fake_cloud: FakeCloud) -> MopekaApiClient:
    """Return a client wired to the fake cloud."""
    return MopekaApiClient(async_get_clientsession(hass), EMAIL, PASSWORD)


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Return the options for the mock config entry."""
    return {CONF_UPDATE_INTERVAL: 5}


@pytest.fixture
def config_entry(hass: HomeAssistant, entry_options: dict[str, Any]) -> MockConfigEntry:
    """Return a config entry for the fake account, added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options=entry_options,
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    return entry


def load_json_fixture(name: str) -> Any:
    """Load a JSON fixture from tests/fixtures."""
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text("utf-8"))


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
        new_callable=PropertyMock,
    ):
        yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return a snapshot that ignores IDs and timestamps, as in Home Assistant."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
