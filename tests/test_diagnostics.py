"""Tests for Mopeka HA diagnostics."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import JSONEncoder
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.mopeka_ha.const import CONF_EXCLUDED_DEVICES
from custom_components.mopeka_ha.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import ADDRESS_A, ADDRESS_B, EMAIL, PASSWORD
from .fake_cloud import FakeCloud


async def test_diagnostics(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Diagnostics include readings and settings but no credentials or IDs."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_EXCLUDED_DEVICES: [ADDRESS_B]}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(result, cls=JSONEncoder)

    for secret in (EMAIL, PASSWORD, ADDRESS_A, ADDRESS_B, "Tank A", "Tank B", "gw-0"):
        assert secret not in text
    assert result["backend"] == "legacy"
    assert result["last_update_success"] is True
    assert result["update_interval_s"] == 300
    assert result["entry"]["data"]["email"] == "**REDACTED**"
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["version"] == "1.2"

    first, second = result["devices"]
    assert first["excluded"] is False
    assert first["info"]["tank_type"] == "20lb"
    assert first["reading"]["level_m"] == 0.167
    assert first["reading"]["timestamp"] == "2025-11-01T12:26:40+00:00"
    assert second["excluded"] is True
    assert second["reading"] is None


async def test_diagnostics_snapshot(
    hass: HomeAssistant,
    fake_cloud: FakeCloud,
    config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """The full diagnostics output matches the reviewed snapshot."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await async_get_config_entry_diagnostics(hass, config_entry) == snapshot
