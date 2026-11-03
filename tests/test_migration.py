"""Tests for migrating entries created before version 2.0."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mopeka_ha import async_migrate_entry
from custom_components.mopeka_ha.const import (
    CONF_SENSORS,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

from .conftest import ADDRESS_A, EMAIL, PASSWORD
from .fake_cloud import FakeCloud

LEGACY_KEYS = (
    "level_cm",
    "level_inches",
    "temperature_f",
    "updated_human_readable",
    "battery_voltage",
    "tank_type",
    "propaneButaneRatio",
)


def _legacy_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a 1.1 entry with pre-2.0 options and entities for Tank A."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={CONF_SENSORS: list(LEGACY_KEYS), CONF_UPDATE_INTERVAL: 1},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in LEGACY_KEYS:
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{ADDRESS_A}_{key}",
            suggested_object_id=f"tank_a_{key.lower()}",
            config_entry=entry,
        )
    return entry


def _entity_id(hass: HomeAssistant, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{ADDRESS_A}_{key}"
    )


async def test_migration_us_units(hass: HomeAssistant, fake_cloud: FakeCloud) -> None:
    """With US units, inch and °F entities are kept and keep their units."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = _legacy_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, 2)
    assert entry.options == {CONF_UPDATE_INTERVAL: 1}

    # Entity IDs are kept, so history and dashboards keep working.
    assert _entity_id(hass, "level") == "sensor.tank_a_level_inches"
    assert registry.async_get("sensor.tank_a_level_cm") is None
    assert _entity_id(hass, "temperature") == "sensor.tank_a_temperature_f"
    assert _entity_id(hass, "last_reading") == "sensor.tank_a_updated_human_readable"
    assert _entity_id(hass, "battery_voltage") == "sensor.tank_a_battery_voltage"
    assert registry.async_get("sensor.tank_a_tank_type") is None
    assert registry.async_get("sensor.tank_a_propanebutaneratio") is None

    level = hass.states.get("sensor.tank_a_level_inches")
    assert level.attributes["unit_of_measurement"] == "in"
    assert float(level.state) == pytest.approx(150 / 25.4, abs=0.01)
    temperature = hass.states.get("sensor.tank_a_temperature_f")
    assert temperature.attributes["unit_of_measurement"] == "°F"
    # Migrated battery voltage stays enabled, unlike new installs.
    assert hass.states.get("sensor.tank_a_battery_voltage").state == "2.94"
    # Measurements that had no entity are created fresh.
    assert hass.states.get("sensor.tank_a_fill_level").state == "50.0"


async def test_migration_metric_units(
    hass: HomeAssistant, fake_cloud: FakeCloud
) -> None:
    """With metric units, the centimetre entity is the one kept."""
    hass.config.units = METRIC_SYSTEM
    entry = _legacy_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _entity_id(hass, "level") == "sensor.tank_a_level_cm"
    assert er.async_get(hass).async_get("sensor.tank_a_level_inches") is None
    level = hass.states.get("sensor.tank_a_level_cm")
    assert level.attributes["unit_of_measurement"] == "cm"
    assert level.state == "15.0"
    # Only °F existed, so it is kept even on a metric system.
    assert _entity_id(hass, "temperature") == "sensor.tank_a_temperature_f"


async def test_migration_ignores_other_entities(
    hass: HomeAssistant, fake_cloud: FakeCloud
) -> None:
    """Entities whose unique IDs are not pre-2.0 keys are left alone."""
    entry = _legacy_entry(hass)
    registry = er.async_get(hass)
    other = registry.async_get_or_create(
        "sensor", DOMAIN, f"{ADDRESS_A}_level", config_entry=entry
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(other.entity_id).unique_id == f"{ADDRESS_A}_level"
    # The new-style entity already holds "level", so both legacy ones go.
    assert registry.async_get("sensor.tank_a_level_cm") is None
    assert registry.async_get("sensor.tank_a_level_inches") is None


async def test_entry_from_newer_release(
    hass: HomeAssistant, fake_cloud: FakeCloud
) -> None:
    """An entry from a newer major version is not loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        version=2,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_migrate_entry_refuses_newer_version(hass: HomeAssistant) -> None:
    """The migration itself also refuses entries from a newer major version.

    Home Assistant checks this before calling it, so this covers the guard
    Home Assistant's developer documentation recommends keeping.
    """
    entry = MockConfigEntry(domain=DOMAIN, version=2)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False
