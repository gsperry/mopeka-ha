"""Sensor platform for Mopeka HA."""

from __future__ import annotations

import re
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SENSORS, DOMAIN
from .coordinator import MopekaConfigEntry, MopekaCoordinator
from .models import MEASUREMENT_KEYS

# Unit, icon, and device class for each numeric measurement.
_NUMERIC: Final[dict[str, tuple[str, str, SensorDeviceClass | None]]] = {
    "tank_height": ("m", "ruler", None),
    "temperature_c": ("C", "thermometer", None),
    "temperature_f": ("F", "thermometer", None),
    "battery_voltage": ("V", "battery", SensorDeviceClass.VOLTAGE),
    "signal_quality": ("%", "signal", None),
    "level_cm": ("cm", "ruler", None),
    "level_inches": ("in", "ruler", None),
    "fill_percent": ("%", "percent", None),
    "volume_liters": ("L", "water", None),
    "volume_gallons_us": ("gal", "water", None),
}
_ICONS: Final = {"vertical": "check-circle", "propaneButaneRatio": "information"}


def _measurement_key(unique_id: str) -> str | None:
    """Return the measurement key at the end of an entity's unique ID."""
    for key in sorted(MEASUREMENT_KEYS, key=len, reverse=True):
        if unique_id.endswith(f"_{key}"):
            return key
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MopekaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for the measurements the user selected."""
    coordinator = entry.runtime_data
    selected = set(entry.options.get(CONF_SENSORS, []))

    # Remove entities for measurements the user has deselected.
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        key = _measurement_key(entity.unique_id)
        if key is not None and key not in selected:
            registry.async_remove(entity.entity_id)

    async_add_entities(
        MopekaSensor(coordinator, address, key)
        for address in coordinator.data.tanks
        for key in MEASUREMENT_KEYS
        if key in selected
    )


class MopekaSensor(CoordinatorEntity[MopekaCoordinator], SensorEntity):
    """One measurement of one Mopeka tank."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MopekaCoordinator, address: str, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._address = address
        self._key = key
        self._attr_unique_id = f"{address}_{key}"
        self._attr_name = (
            re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ").title()
        )
        if key in _NUMERIC:
            unit, icon, device_class = _NUMERIC[key]
            self._attr_native_unit_of_measurement = unit
            self._attr_device_class = device_class
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            icon = _ICONS.get(key, "text")
        self._attr_icon = f"mdi:{icon}"

        device = coordinator.data.tanks[address].device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=device.name,
            manufacturer="Mopeka",
            model=device.model_number,
            serial_number=address,
        )

    @property
    def available(self) -> bool:
        """Return False while the tank is missing from the cloud response."""
        return super().available and self._address in self.coordinator.data.tanks

    @property
    def native_value(self) -> Any:
        """Return the current value of the measurement."""
        tank = self.coordinator.data.tanks.get(self._address)
        return tank.values().get(self._key) if tank else None
