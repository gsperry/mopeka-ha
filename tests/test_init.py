"""Tests for Mopeka HA setup, unload, and the update coordinator."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mopeka_ha import async_remove_config_entry_device
from custom_components.mopeka_ha.const import (
    CONF_EXCLUDED_DEVICES,
    CONF_SENSORS,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.mopeka_ha.coordinator import update_interval_minutes

from .conftest import ADDRESS_A, ADDRESS_B, EMAIL
from .fake_cloud import FakeCloud


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """The entry loads, reads both tanks, and unloads."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert set(config_entry.runtime_data.data.tanks) == {ADDRESS_A, ADDRESS_B}

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A rejected password at startup asks the user to sign in again."""
    fake_cloud.cognito.users[EMAIL].password = "changed"
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_setup_connection_failure_retries(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A cloud outage at startup retries setup later."""
    fake_cloud.cognito.queue_error("InternalErrorException", "", 500)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_poll_reuses_token_and_survives_outage(
    hass: HomeAssistant,
    fake_cloud: FakeCloud,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Scheduled polls reuse the token; an outage marks entities unavailable."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    fake_cloud.gateway.queued_exceptions.append(TimeoutError())
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    assert hass.states.get("sensor.tank_a_fill_percent").state == "unavailable"

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is True
    assert hass.states.get("sensor.tank_a_fill_percent").state == "50.0"
    assert fake_cloud.cognito.count("InitiateAuth", "USER_SRP_AUTH") == 1


async def test_poll_auth_failure_starts_reauth(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """If the password stops working while running, re-authentication starts."""
    await _setup(hass, config_entry)
    fake_cloud.cognito.users[EMAIL].password = "changed"
    fake_cloud.cognito.expire_all_tokens()
    fake_cloud.cognito.revoke_refresh_tokens()

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


@pytest.mark.parametrize(
    ("options", "minutes"),
    [
        ({}, 5),
        ({CONF_UPDATE_INTERVAL: 1}, 1),
        ({CONF_UPDATE_INTERVAL: 0}, 1),
        ({CONF_UPDATE_INTERVAL: 1000}, 60),
        ({CONF_UPDATE_INTERVAL: "15"}, 15),
        ({CONF_UPDATE_INTERVAL: "often"}, 5),
        ({CONF_UPDATE_INTERVAL: None}, 5),
    ],
)
def test_update_interval(options: dict[str, Any], minutes: int) -> None:
    """The interval option is clamped and defaults sensibly."""
    assert update_interval_minutes(options) == minutes


@pytest.mark.parametrize(
    "entry_options",
    [{CONF_SENSORS: ["fill_percent"], CONF_EXCLUDED_DEVICES: [ADDRESS_B]}],
)
async def test_excluded_device_is_removed(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A device the user excluded is detached from the entry at setup."""
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, ADDRESS_B)}
    )
    await _setup(hass, config_entry)

    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, ADDRESS_B), config_entry.entry_id
        )
        is None
    )
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, ADDRESS_A), config_entry.entry_id
        )
        is not None
    )
    assert not any(ADDRESS_B in path for path, _ in fake_cloud.gateway.requests)


async def test_remove_config_entry_device(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Only devices the account no longer reports can be deleted."""
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    current = registry.async_get_device_by_identifier(
        (DOMAIN, ADDRESS_A), config_entry.entry_id
    )
    stale = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "OLD:00")}
    )
    assert current is not None

    assert not await async_remove_config_entry_device(hass, config_entry, current)
    assert await async_remove_config_entry_device(hass, config_entry, stale)
