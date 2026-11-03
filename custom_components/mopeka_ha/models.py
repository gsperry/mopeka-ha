"""Data models for Mopeka Cloud responses.

Parsing is deliberately forgiving: a missing or malformed field becomes ``None``
instead of raising, so one odd sensor cannot break the whole account.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

# Litres per unit of tank capacity, keyed by the unit in the app's tank type.
_LITRES_PER_UNIT: Final = {
    "l": 1.0,
    "lb": 0.925,
    "gal": 3.78541,
    "us_gal": 3.78541,
    "imp_gal": 4.54609,
}

# Battery voltage range mapped to 0-100 %, as in Home Assistant's Bluetooth
# Mopeka integration.
_BATTERY_EMPTY_V: Final = 2.2
_BATTERY_SPAN_V: Final = 0.65

# Offset subtracted from the older platform's level reading, in metres. Carried
# over from the upstream client; its origin is undocumented.
_LEGACY_LEVEL_OFFSET_M: Final = 0.017

# Converts a sensor's raw echo time to millimetres of propane, as a quadratic in
# the sensor's raw temperature byte (degrees Celsius plus 40). These are the
# propane coefficients used by Home Assistant's Bluetooth Mopeka integration.
_PROPANE_LEVEL_COEFFICIENTS: Final = (0.573045, -0.002822, -0.00000535)
_RAW_TEMPERATURE_OFFSET: Final = 40

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


def raw_level_to_m(raw_level: float, temperature_c: float) -> float:
    """Convert a raw echo-time reading to a propane level in metres."""
    temp = temperature_c + _RAW_TEMPERATURE_OFFSET
    c0, c1, c2 = _PROPANE_LEVEL_COEFFICIENTS
    return max(0.0, raw_level * (c0 + c1 * temp + c2 * temp**2) / 1000)


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

    @property
    def display_name(self) -> str:
        """Return the device name, or a short name built from its address."""
        if self.name:
            return self.name
        suffix = self.address.replace(":", "").replace("-", "")[-4:].upper()
        return f"Mopeka {suffix}"

    @classmethod
    def from_retail(cls, data: dict[str, Any]) -> MopekaDevice | None:
        """Build a device from the retail sync response, or None without a MAC."""
        address = _to_str(data.get("mac"))
        if address is None:
            return None
        return cls(
            address=address,
            name=_to_str(data.get("name")),
            model_number=None,
            tank_type=None,
            tank_height=None,
            vertical=None,
            propane_butane_ratio=None,
        )

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
    # Subtracted from level_m before use. Only the older platform needs it.
    level_offset_m: float = 0.0
    # The current platform's "q" value. Its scale is not yet known.
    raw_quality: float | None = None

    @classmethod
    def from_retail(cls, sample: dict[str, Any]) -> MopekaReading:
        """Build a reading from one sample in the retail sync response.

        Samples are decoded sensor broadcasts: t is the time in Unix seconds, r
        the raw echo time, c the temperature in degrees Celsius, v the battery
        voltage, and q a quality value of unknown scale.
        """
        timestamp_s = _to_float(sample.get("t"))
        raw_level = _to_float(sample.get("r"))
        temperature = _to_float(sample.get("c"))
        level = (
            raw_level_to_m(raw_level, temperature)
            if raw_level is not None and temperature is not None
            else None
        )
        return cls(
            timestamp=(
                datetime.fromtimestamp(timestamp_s, tz=UTC)
                if timestamp_s is not None and timestamp_s > 0
                else None
            ),
            level_m=level,
            temperature_c=temperature,
            battery_voltage=_to_float(sample.get("v")),
            quality=None,
            gateway_id=None,
            raw_quality=_to_float(sample.get("q")),
        )

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
            level_offset_m=_LEGACY_LEVEL_OFFSET_M,
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


@dataclass(frozen=True, slots=True)
class MopekaTank:
    """A device together with its latest reading."""

    device: MopekaDevice
    reading: MopekaReading | None = None

    @property
    def level_m(self) -> float | None:
        """Return the liquid level in metres, after any calibration offset."""
        if self.reading is None or self.reading.level_m is None:
            return None
        raw = self.reading.level_m
        return max(0.0, raw - self.reading.level_offset_m) if raw > 0 else 0.0

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

    @property
    def level_mm(self) -> float | None:
        """Return the liquid level in millimetres."""
        level = self.level_m
        return round(level * 1000, 1) if level is not None else None

    @property
    def temperature_c(self) -> float | None:
        """Return the temperature at the sensor, in degrees Celsius."""
        return self.reading.temperature_c if self.reading else None

    @property
    def battery_voltage(self) -> float | None:
        """Return the sensor's battery voltage."""
        return self.reading.battery_voltage if self.reading else None

    @property
    def battery_percent(self) -> float | None:
        """Return the battery level, in percent, estimated from its voltage."""
        voltage = self.battery_voltage
        if voltage is None:
            return None
        percent = (voltage - _BATTERY_EMPTY_V) / _BATTERY_SPAN_V * 100
        return round(max(0.0, min(100.0, percent)), 1)

    @property
    def last_reading(self) -> datetime | None:
        """Return when the sensor took its latest reading."""
        return self.reading.timestamp if self.reading else None

    @property
    def signal_quality(self) -> float | None:
        """Return the reading's quality value, on the platform's own scale."""
        if self.reading is None:
            return None
        if self.reading.quality is not None:
            return self.reading.quality
        return self.reading.raw_quality
