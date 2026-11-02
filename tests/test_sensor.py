"""Tests for the Mopeka HA sensor platform."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mopeka_ha.const import CONF_SENSORS, DOMAIN
from custom_components.mopeka_ha.models import MEASUREMENT_KEYS

from .conftest import ADDRESS_A, ADDRESS_B
from .fake_cloud import FakeCloud, make_device, make_reading


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensor_states(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Selected measurements are created for each tank with correct values."""
    await _setup(hass, config_entry)

    assert hass.states.get("sensor.tank_a_fill_percent").state == "50.0"
    assert hass.states.get("sensor.tank_a_battery_voltage").state == "2.94"
    assert hass.states.get("sensor.tank_a_temperature_c").state == "21"
    # Tank B is horizontal; half its height is half its volume.
    assert hass.states.get("sensor.tank_b_fill_percent").state == "50.0"
    assert hass.states.get("sensor.tank_b_temperature_c").state == "8"
    # Measurements that were not selected are not created.
    assert hass.states.get("sensor.tank_a_level_cm") is None

    battery = hass.states.get("sensor.tank_a_battery_voltage")
    assert battery.attributes["unit_of_measurement"] == "V"
    assert battery.attributes["device_class"] == "voltage"
    assert battery.attributes["state_class"] == "measurement"


async def test_unique_ids_and_device_info(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Unique IDs keep the pre-fork format, and each tank is a device."""
    await _setup(hass, config_entry)

    entity = er.async_get(hass).async_get("sensor.tank_a_fill_percent")
    assert entity.unique_id == f"{ADDRESS_A}_fill_percent"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS_A), config_entry.entry_id
    )
    assert device.name == "Tank A"
    assert device.manufacturer == "Mopeka"
    assert device.model == "M1017"
    assert device.serial_number == ADDRESS_A
    assert entity.device_id == device.id


@pytest.mark.parametrize("entry_options", [{CONF_SENSORS: list(MEASUREMENT_KEYS)}])
async def test_every_measurement(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Every measurement can be created and has a value."""
    await _setup(hass, config_entry)
    states = {
        s.entity_id: s.state
        for s in hass.states.async_all("sensor")
        if s.entity_id.startswith("sensor.tank_a_")
    }
    assert len(states) == len(MEASUREMENT_KEYS)
    assert STATE_UNKNOWN not in states.values()
    assert states["sensor.tank_a_vertical"] == "True"
    assert states["sensor.tank_a_tank_type"] == "20lb"
    assert states["sensor.tank_a_volume_original_unit"] == "lb"
    assert states["sensor.tank_a_level_cm"] == "15.0"
    assert states["sensor.tank_a_temperature_f"] == "70"
    assert states["sensor.tank_a_propane_butane_ratio"] == "1.0"
    assert states["sensor.tank_a_updated_human_readable"].endswith("ago")


async def test_tank_drops_out_and_returns(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A tank missing from the cloud response goes unavailable, then recovers."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    tank_b = fake_cloud.gateway.devices.pop()

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tank_b_fill_percent").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.tank_a_fill_percent").state == "50.0"

    fake_cloud.gateway.devices.append(tank_b)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tank_b_fill_percent").state == "50.0"


async def test_tank_without_height(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A tank with no height set shows fill as unknown instead of failing setup."""
    fake_cloud.gateway.devices.clear()
    fake_cloud.gateway.add_tank(
        make_device(ADDRESS_A, "Tank A", tank_height=None, tank_type="unknown"),
        make_reading(),
    )
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.tank_a_fill_percent").state == STATE_UNKNOWN
    assert hass.states.get("sensor.tank_a_battery_voltage").state == "2.94"


async def test_deselected_measurement_is_removed(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Entities for measurements removed in options are deleted on reload."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    assert registry.async_get("sensor.tank_a_temperature_c") is not None

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_SENSORS: ["fill_percent"]}
    )
    await hass.async_block_till_done()

    assert registry.async_get("sensor.tank_a_temperature_c") is None
    assert registry.async_get("sensor.tank_a_fill_percent") is not None
    assert registry.async_get("sensor.tank_b_fill_percent") is not None
    assert ADDRESS_B in config_entry.runtime_data.data.tanks
