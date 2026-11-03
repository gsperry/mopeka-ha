"""The Mopeka HA integration."""

from __future__ import annotations

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MopekaApiClient
from .const import CONF_BACKEND, DOMAIN
from .coordinator import MopekaConfigEntry, MopekaCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


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
