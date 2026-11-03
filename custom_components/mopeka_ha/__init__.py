"""The Mopeka HA integration."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    Platform,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.unit_system import METRIC_SYSTEM

from .api import MopekaApiClient
from .const import (
    CONF_BACKEND,
    CONF_SENSORS,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from .coordinator import MopekaConfigEntry, MopekaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Measurement keys of releases before 2.0, as used in entity unique IDs, mapped
# to the new key and the display unit to keep. None means the entity is removed.
_LEGACY_ENTITY_KEYS: Final[dict[str, tuple[str, str | None] | None]] = {
    "level_cm": ("level", UnitOfLength.CENTIMETERS),
    "level_inches": ("level", UnitOfLength.INCHES),
    "temperature_c": ("temperature", UnitOfTemperature.CELSIUS),
    "temperature_f": ("temperature", UnitOfTemperature.FAHRENHEIT),
    "volume_liters": ("volume", UnitOfVolume.LITERS),
    "volume_gallons_us": ("volume", UnitOfVolume.GALLONS),
    "updated_human_readable": ("last_reading", None),
    "battery_voltage": ("battery_voltage", None),
    "fill_percent": ("fill_percent", None),
    "signal_quality": ("signal_quality", None),
    "propaneButaneRatio": None,
    "tank_height": None,
    "tank_type": None,
    "vertical": None,
    "volume_original_unit": None,
}
_METRIC_KEYS: Final = frozenset({"level_cm", "temperature_c", "volume_liters"})
_US_KEYS: Final = frozenset({"level_inches", "temperature_f", "volume_gallons_us"})


async def async_migrate_entry(hass: HomeAssistant, entry: MopekaConfigEntry) -> bool:
    """Migrate entries from releases before 2.0."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False  # Created by a newer release.
    if entry.minor_version < CONFIG_ENTRY_MINOR_VERSION:
        _migrate_legacy_entities(hass, entry)
        options = {k: v for k, v in entry.options.items() if k != CONF_SENSORS}
        hass.config_entries.async_update_entry(
            entry, options=options, minor_version=CONFIG_ENTRY_MINOR_VERSION
        )
        _LOGGER.debug("Migrated entry %s to the current version", entry.entry_id)
    return True


def _split_legacy_unique_id(unique_id: str) -> tuple[str, str] | None:
    """Return (address, legacy key) for a pre-2.0 unique ID, or None."""
    for key in sorted(_LEGACY_ENTITY_KEYS, key=len, reverse=True):
        if unique_id.endswith(f"_{key}"):
            return unique_id[: -len(key) - 1], key
    return None


def _migrate_legacy_entities(hass: HomeAssistant, entry: MopekaConfigEntry) -> None:
    """Move pre-2.0 entities to the new keys, keeping entity IDs and history.

    Where a measurement existed in two units, the entity in the unit system
    Home Assistant uses is kept and the other removed. Kept entities go on
    showing the unit they had.
    """
    registry = er.async_get(hass)
    preferred = _METRIC_KEYS if hass.config.units is METRIC_SYSTEM else _US_KEYS
    taken = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    legacy = [
        (entity, parts)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if (parts := _split_legacy_unique_id(entity.unique_id)) is not None
    ]
    legacy.sort(key=lambda item: item[1][1] not in preferred)
    for entity, (address, old_key) in legacy:
        target = _LEGACY_ENTITY_KEYS[old_key]
        if target is None:
            registry.async_remove(entity.entity_id)
            continue
        new_key, unit = target
        new_unique_id = f"{address}_{new_key}"
        if new_unique_id == entity.unique_id:
            continue
        if new_unique_id in taken:
            registry.async_remove(entity.entity_id)
            continue
        registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)
        taken.add(new_unique_id)
        if unit is not None:
            registry.async_update_entity_options(
                entity.entity_id, "sensor", {"unit_of_measurement": unit}
            )


async def async_setup_entry(hass: HomeAssistant, entry: MopekaConfigEntry) -> bool:
    """Set up Mopeka HA from a config entry."""
    client = MopekaApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_BACKEND),
    )
    coordinator = MopekaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Entries created before platform detection learn their platform here.
    if client.backend and entry.data.get(CONF_BACKEND) != client.backend:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_BACKEND: client.backend}
        )

    _remove_excluded_devices(hass, entry, coordinator.excluded_devices)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MopekaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: MopekaConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a device that the account no longer reports."""
    tanks = entry.runtime_data.data.tanks
    return not any(
        identifier[0] == DOMAIN and identifier[1] in tanks
        for identifier in device.identifiers
    )


async def _async_update_listener(hass: HomeAssistant, entry: MopekaConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_excluded_devices(
    hass: HomeAssistant, entry: MopekaConfigEntry, excluded: frozenset[str]
) -> None:
    """Detach devices the user excluded, along with their entities."""
    if not excluded:
        return
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(
            identifier[0] == DOMAIN and identifier[1] in excluded
            for identifier in device.identifiers
        ):
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
