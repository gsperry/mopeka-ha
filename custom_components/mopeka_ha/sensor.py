"""Sensor platform for Mopeka HA."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MopekaConfigEntry, MopekaCoordinator
from .models import MopekaTank, parse_tank_type


@dataclass(frozen=True, kw_only=True)
class MopekaSensorEntityDescription(SensorEntityDescription):
    """Describes a Mopeka tank sensor."""

    value_fn: Callable[[MopekaTank], float | datetime | None]
    # Whether a tank can ever report this value. Tanks on the current platform
    # have no size settings, so they never report fill or volume.
    exists_fn: Callable[[MopekaTank], bool] = lambda _: True


SENSORS: tuple[MopekaSensorEntityDescription, ...] = (
    MopekaSensorEntityDescription(
        key="level",
        translation_key="level",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda tank: tank.level_mm,
    ),
    MopekaSensorEntityDescription(
        key="fill_percent",
        translation_key="fill_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda tank: tank.fill_percent,
        exists_fn=lambda tank: bool(tank.device.tank_height),
    ),
    MopekaSensorEntityDescription(
        key="volume",
        translation_key="volume",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda tank: tank.volume_litres,
        exists_fn=lambda tank: (
            bool(tank.device.tank_height)
            and parse_tank_type(tank.device.tank_type)[0] is not None
        ),
    ),
    MopekaSensorEntityDescription(
        key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda tank: tank.temperature_c,
    ),
    MopekaSensorEntityDescription(
        key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda tank: tank.battery_percent,
    ),
    MopekaSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda tank: tank.battery_voltage,
    ),
    MopekaSensorEntityDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda tank: tank.last_reading,
    ),
    MopekaSensorEntityDescription(
        key="signal_quality",
        translation_key="signal_quality",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda tank: tank.signal_quality,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MopekaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for each tank with a cloud reading."""
    coordinator = entry.runtime_data
    async_add_entities(
        MopekaSensor(coordinator, address, description)
        for address, tank in coordinator.data.tanks.items()
        for description in SENSORS
        if description.exists_fn(tank)
    )


class MopekaSensor(CoordinatorEntity[MopekaCoordinator], SensorEntity):
    """One measurement of one Mopeka tank."""

    _attr_has_entity_name = True
    entity_description: MopekaSensorEntityDescription

    def __init__(
        self,
        coordinator: MopekaCoordinator,
        address: str,
        description: MopekaSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._address = address
        self._attr_unique_id = f"{address}_{description.key}"
        device = coordinator.data.tanks[address].device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=device.display_name,
            manufacturer="Mopeka",
            model=device.model_number,
            serial_number=address,
        )

    @property
    def available(self) -> bool:
        """Return False while the tank is missing from the cloud response."""
        return super().available and self._address in self.coordinator.data.tanks

    @property
    def native_value(self) -> float | datetime | None:
        """Return the current value."""
        tank = self.coordinator.data.tanks.get(self._address)
        return self.entity_description.value_fn(tank) if tank else None
