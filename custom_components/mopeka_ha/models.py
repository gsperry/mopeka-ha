"""Data models for Mopeka Cloud responses.

Parsing is deliberately forgiving: a missing or malformed field becomes ``None``
instead of raising, so one odd sensor cannot break the whole account.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

# Measurement keys exposed as sensors. These are the keys used by releases
# before the fork; they are part of each entity's unique ID, so they must not
# change.
MEASUREMENT_KEYS: Final = (
    "battery_voltage",
    "fill_percent",
    "level_cm",
    "level_inches",
    "propaneButaneRatio",
    "signal_quality",
    "tank_height",
    "tank_type",
    "temperature_c",
    "temperature_f",
    "updated_human_readable",
    "vertical",
    "volume_gallons_us",
    "volume_liters",
    "volume_original_unit",
)

# Litres per unit of tank capacity, keyed by the unit in the app's tank type.
_LITRES_PER_UNIT: Final = {
    "l": 1.0,
    "lb": 0.925,
    "gal": 3.78541,
    "us_gal": 3.78541,
    "imp_gal": 4.54609,
}
_LITRES_PER_US_GALLON: Final = 3.78541
_INCHES_PER_METRE: Final = 39.3701

# Offset subtracted from the raw level reading, in metres. Carried over from
# the upstream client; its origin is undocumented.
_LEVEL_OFFSET_M: Final = 0.017

# Length of a horizontal tank's cylindrical section, in multiples of its radius.
# Carried over from the upstream client; it is an assumption, not a measurement.
_HORIZONTAL_LENGTH_RADII: Final = 4.5

_TANK_TYPE_RE: Final = re.compile(r"^([\d.]+)\s*([a-zA-Z_]+)$")


def _to_float(value: Any) -> float | None:
    """Return value as a float, or None if it is not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _to_bool(value: Any) -> bool | None:
    """Return value as a bool, accepting the string forms the API uses."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
    if isinstance(value, int | float):
        return bool(value)
    return None


def _to_str(value: Any) -> str | None:
    """Return value as a non-empty string, or None."""
    if value is None or isinstance(value, dict | list):
        return None
    text = str(value).strip()
    return text or None


def ddb_value(item: dict[str, Any], key: str) -> Any:
    """Return a plain value from a DynamoDB-typed attribute such as {"N": "1.5"}."""
    attribute = item.get(key)
    if not isinstance(attribute, dict):
        return None
    if "N" in attribute:
        return _to_float(attribute["N"])
    if "S" in attribute:
        return attribute["S"]
    if "BOOL" in attribute:
        return _to_bool(attribute["BOOL"])
    return None


@dataclass(frozen=True, slots=True)
class MopekaDevice:
    """A sensor registered to the account, from the gateway's device list."""

    address: str
    name: str | None
    model_number: str | None
    tank_type: str | None
    tank_height: float | None
    vertical: bool | None
    propane_butane_ratio: float | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> MopekaDevice | None:
        """Build a device from the gateway response, or None if it has no address."""
        address = _to_str(data.get("address"))
        if address is None:
            return None
        return cls(
            address=address,
            name=_to_str(data.get("name")),
            model_number=_to_str(data.get("modelNumber")),
            tank_type=_to_str(data.get("tankType")),
            tank_height=_to_float(data.get("tankHeight")),
            vertical=_to_bool(data.get("vertical")),
            propane_butane_ratio=_to_float(data.get("propaneButaneRatio")),
        )


@dataclass(frozen=True, slots=True)
class MopekaReading:
    """The most recent reading the cloud holds for one sensor."""

    timestamp: datetime | None
    level_m: float | None
    temperature_c: float | None
    battery_voltage: float | None
    quality: float | None
    gateway_id: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> MopekaReading:
        """Build a reading from one DynamoDB item in the data-shadow response."""
        timestamp_ms = ddb_value(item, "Timestamp")
        timestamp = (
            datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
            if isinstance(timestamp_ms, float) and timestamp_ms > 0
            else None
        )
        return cls(
            timestamp=timestamp,
            level_m=_to_float(ddb_value(item, "Value")),
            temperature_c=_to_float(ddb_value(item, "Temp")),
            battery_voltage=_to_float(ddb_value(item, "BatteryLevel")),
            quality=_to_float(ddb_value(item, "Quality")),
            gateway_id=_to_str(ddb_value(item, "Source")),
        )


def parse_tank_type(tank_type: str | None) -> tuple[float | None, str | None]:
    """Split a tank type such as "20lb" into capacity and lower-case unit."""
    if not tank_type:
        return None, None
    match = _TANK_TYPE_RE.match(tank_type.strip())
    if match is None:
        return None, None
    return _to_float(match.group(1)), match.group(2).lower()


def calculate_fill_percent(level: float, height: float, vertical: bool) -> float:
    """Return how full a tank is, in percent, from its liquid level and height."""
    if height <= 0:
        return 0.0
    ratio = max(0.0, min(1.0, level / height))
    if vertical or ratio <= 0 or ratio >= 1:
        return round(ratio * 100.0, 2)
    # Horizontal tank: a cylinder with hemispherical heads.
    radius = height / 2.0
    length = _HORIZONTAL_LENGTH_RADII
    angle = math.acos(1 - 2 * ratio)
    cylinder = (angle - (1 - 2 * ratio) * math.sin(angle)) * radius**2 * length * radius
    heads = math.pi * level**2 * (3 * radius - level) / 3.0
    full = math.pi * radius**2 * length * radius + 4.0 / 3.0 * math.pi * radius**3
    return round((cylinder + heads) / full * 100.0, 2)


def _human_age(timestamp: datetime | None, now: float) -> str | None:
    if timestamp is None:
        return None
    age = int(now - timestamp.timestamp())
    if age < 60:
        return "Just now"
    if age < 3600:
        return f"{age // 60} min ago"
    if age < 86400:
        return f"{age // 3600} hours ago"
    return f"{age // 86400} days ago"


@dataclass(frozen=True, slots=True)
class MopekaTank:
    """A device together with its latest reading."""

    device: MopekaDevice
    reading: MopekaReading | None = None
    _now: float = field(default_factory=time.time, compare=False, repr=False)

    @property
    def level_m(self) -> float | None:
        """Return the liquid level in metres, after the calibration offset."""
        if self.reading is None or self.reading.level_m is None:
            return None
        raw = self.reading.level_m
        return max(0.0, raw - _LEVEL_OFFSET_M) if raw > 0 else 0.0

    @property
    def fill_percent(self) -> float | None:
        """Return how full the tank is, in percent."""
        level = self.level_m
        height = self.device.tank_height
        if level is None or height is None or height <= 0:
            return None
        return calculate_fill_percent(level, height, bool(self.device.vertical))

    @property
    def volume_litres(self) -> float | None:
        """Return the estimated liquid volume in litres."""
        fill = self.fill_percent
        capacity, unit = parse_tank_type(self.device.tank_type)
        if fill is None or not capacity or not unit:
            return None
        return round(capacity * fill / 100.0 * _LITRES_PER_UNIT.get(unit, 1.0), 2)

    def values(self) -> dict[str, Any]:
        """Return every measurement, keyed by MEASUREMENT_KEYS."""
        reading = self.reading
        level = self.level_m
        litres = self.volume_litres
        temp_c = reading.temperature_c if reading else None
        battery = reading.battery_voltage if reading else None
        quality = reading.quality if reading else None
        return {
            "battery_voltage": round(battery, 2) if battery is not None else None,
            "fill_percent": self.fill_percent,
            "level_cm": round(level * 100.0, 1) if level is not None else None,
            "level_inches": (
                round(level * _INCHES_PER_METRE, 2) if level is not None else None
            ),
            "propaneButaneRatio": self.device.propane_butane_ratio,
            "signal_quality": int(quality) if quality is not None else None,
            "tank_height": self.device.tank_height,
            "tank_type": self.device.tank_type,
            "temperature_c": int(temp_c) if temp_c is not None else None,
            "temperature_f": (
                int(round(temp_c * 9 / 5 + 32)) if temp_c is not None else None
            ),
            "updated_human_readable": _human_age(
                reading.timestamp if reading else None, self._now
            ),
            "vertical": self.device.vertical,
            "volume_gallons_us": (
                round(litres / _LITRES_PER_US_GALLON, 2) if litres is not None else None
            ),
            "volume_liters": litres,
            "volume_original_unit": parse_tank_type(self.device.tank_type)[1],
        }
