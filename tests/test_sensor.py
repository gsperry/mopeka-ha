"""Tests for the Mopeka HA sensor platform."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mopeka_ha.const import DOMAIN

from .conftest import ADDRESS_A
from .fake_cloud import FakeCloud, make_device, make_reading


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensor_states(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Each tank gets level, fill, volume, temperature, battery, and last reading."""
    await _setup(hass, config_entry)

    level = hass.states.get("sensor.tank_a_level")
    assert level.state == "150.0"
    assert level.attributes["unit_of_measurement"] == "mm"
    assert level.attributes["device_class"] == "distance"
    assert level.attributes["state_class"] == "measurement"
    assert level.attributes["friendly_name"] == "Tank A Level"

    assert hass.states.get("sensor.tank_a_fill_level").state == "50.0"
    assert hass.states.get("sensor.tank_a_volume").state == "9.25"
    assert hass.states.get("sensor.tank_a_temperature").state == "21.0"
    assert hass.states.get("sensor.tank_a_battery").state == "100.0"
    assert (
        hass.states.get("sensor.tank_a_last_reading").state
        == "2025-11-01T12:26:40+00:00"
    )
    # Tank B is horizontal; half its height is half its volume.
    assert hass.states.get("sensor.tank_b_level").state == "450.0"
    assert hass.states.get("sensor.tank_b_fill_level").state == "50.0"
    assert hass.states.get("sensor.tank_b_volume").state == "189.27"
    assert hass.states.get("sensor.tank_b_battery").state == "78.5"


async def test_diagnostic_and_disabled_entities(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Battery and last reading are diagnostic; voltage and quality start disabled."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    for entity_id in ("sensor.tank_a_battery", "sensor.tank_a_last_reading"):
        entry = registry.async_get(entity_id)
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert entry.disabled_by is None
    for entity_id in ("sensor.tank_a_battery_voltage", "sensor.tank_a_reading_quality"):
        entry = registry.async_get(entity_id)
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(entity_id) is None


async def test_us_customary_units(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """With US units, level shows in inches, temperature in °F, volume in gallons."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _setup(hass, config_entry)

    level = hass.states.get("sensor.tank_a_level")
    assert level.attributes["unit_of_measurement"] == "in"
    assert float(level.state) == pytest.approx(150 / 25.4, abs=0.01)
    temperature = hass.states.get("sensor.tank_a_temperature")
    assert temperature.attributes["unit_of_measurement"] == "°F"
    assert float(temperature.state) == pytest.approx(69.8, abs=0.1)
    volume = hass.states.get("sensor.tank_a_volume")
    assert volume.attributes["unit_of_measurement"] == "gal"
    assert float(volume.state) == pytest.approx(9.25 / 3.78541, abs=0.01)


async def test_unique_ids_and_device_info(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Unique IDs combine address and key, and each tank is a device."""
    await _setup(hass, config_entry)

    entity = er.async_get(hass).async_get("sensor.tank_a_level")
    assert entity.unique_id == f"{ADDRESS_A}_level"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS_A), config_entry.entry_id
    )
    assert device.name == "Tank A"
    assert device.manufacturer == "Mopeka"
    assert device.model == "M1017"
    assert device.serial_number == ADDRESS_A
    assert entity.device_id == device.id


async def test_tank_without_size(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Without a height or capacity, fill and volume are not created."""
    fake_cloud.gateway.devices.clear()
    fake_cloud.gateway.add_tank(
        make_device(ADDRESS_A, "Tank A", tank_height=None, tank_type="unknown"),
        make_reading(),
    )
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.tank_a_level").state == "150.0"
    assert hass.states.get("sensor.tank_a_fill_level") is None
    assert hass.states.get("sensor.tank_a_volume") is None


async def test_tank_with_height_but_no_capacity(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """With a height but no capacity, fill is created but volume is not."""
    fake_cloud.gateway.devices.clear()
    fake_cloud.gateway.add_tank(
        make_device(ADDRESS_A, "Tank A", tank_type="custom"), make_reading()
    )
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.tank_a_fill_level").state == "50.0"
    assert hass.states.get("sensor.tank_a_volume") is None


async def test_missing_values_are_unknown(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A reading without a value shows unknown, not an error."""
    reading = make_reading()
    del reading["Temp"], reading["BatteryLevel"]
    fake_cloud.gateway.readings[ADDRESS_A] = [reading]
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.tank_a_temperature").state == STATE_UNKNOWN
    assert hass.states.get("sensor.tank_a_battery").state == STATE_UNKNOWN
    assert hass.states.get("sensor.tank_a_level").state == "150.0"


async def test_tank_drops_out_and_returns(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """A tank missing from the cloud response goes unavailable, then recovers."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    tank_b = fake_cloud.gateway.devices.pop()

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tank_b_level").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.tank_a_level").state == "150.0"

    fake_cloud.gateway.devices.append(tank_b)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tank_b_level").state == "450.0"
