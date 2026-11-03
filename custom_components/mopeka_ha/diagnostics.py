"""Diagnostics for Mopeka HA."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_EXCLUDED_DEVICES
from .coordinator import MopekaConfigEntry

# Credentials, and anything that identifies the account, a sensor, or a gateway.
TO_REDACT = {
    CONF_EMAIL,
    CONF_EXCLUDED_DEVICES,
    CONF_PASSWORD,
    "address",
    "gateway_id",
    "name",
    "title",
    "unique_id",
}


def _serializable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serializable(v) for k, v in value.items()}
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MopekaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with personal data redacted."""
    coordinator = entry.runtime_data
    data = coordinator.data
    excluded = coordinator.excluded_devices
    devices = []
    for index, (address, device) in enumerate(data.devices.items(), start=1):
        tank = data.tanks.get(address)
        devices.append(
            async_redact_data(
                {
                    "device": f"device_{index}",
                    "excluded": address in excluded,
                    "info": asdict(device),
                    "reading": _serializable(asdict(tank.reading))
                    if tank and tank.reading
                    else None,
                },
                TO_REDACT,
            )
        )
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "version": f"{entry.version}.{entry.minor_version}",
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "backend": coordinator.client.backend,
        "last_update_success": coordinator.last_update_success,
        "update_interval_s": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "devices": devices,
    }
