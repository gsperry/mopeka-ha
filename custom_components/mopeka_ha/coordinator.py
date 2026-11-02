"""Data update coordinator for Mopeka HA."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MopekaAccountData, MopekaApiClient, MopekaAuthError, MopekaError
from .const import (
    CONF_EXCLUDED_DEVICES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type MopekaConfigEntry = ConfigEntry[MopekaCoordinator]


def update_interval_minutes(options: Mapping[str, Any]) -> int:
    """Return the configured poll interval, clamped to the allowed range."""
    try:
        minutes = int(options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    except (TypeError, ValueError):
        return DEFAULT_UPDATE_INTERVAL
    return max(MIN_UPDATE_INTERVAL, min(MAX_UPDATE_INTERVAL, minutes))


class MopekaCoordinator(DataUpdateCoordinator[MopekaAccountData]):
    """Poll one Mopeka account."""

    config_entry: MopekaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MopekaConfigEntry,
        client: MopekaApiClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_interval_minutes(entry.options)),
        )
        self.client = client

    @property
    def excluded_devices(self) -> frozenset[str]:
        """Return the addresses of devices the user chose not to add."""
        return frozenset(self.config_entry.options.get(CONF_EXCLUDED_DEVICES, []))

    async def _async_update_data(self) -> MopekaAccountData:
        try:
            return await self.client.get_account_data(self.excluded_devices)
        except MopekaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MopekaError as err:
            raise UpdateFailed(str(err)) from err
