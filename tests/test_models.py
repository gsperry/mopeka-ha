"""Unit tests for parsing and calculations in models.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from custom_components.mopeka_ha.models import (
    MopekaDevice,
    MopekaReading,
    MopekaTank,
    calculate_fill_percent,
    ddb_value,
    parse_tank_type,
)


def _device(**overrides: Any) -> MopekaDevice:
    data = {
        "address": "AA:BB:CC:00:00:01",
        "name": "Tank",
        "modelNumber": "M1017",
        "tankType": "20lb",
        "tankHeight": 0.3,
        "vertical": True,
        "propaneButaneRatio": 1,
    }
    device = MopekaDevice.from_api({**data, **overrides})
    assert device is not None
    return device


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ({"N": "1.5"}, 1.5),
        ({"N": "42"}, 42.0),
        ({"N": "nan"}, None),
        ({"N": "inf"}, None),
        ({"N": "abc"}, None),
        ({"S": "text"}, "text"),
        ({"BOOL": True}, True),
        ({"BOOL": "false"}, False),
        ({"NULL": True}, None),
        ("not a dict", None),
    ],
)
def test_ddb_value(attribute: Any, expected: Any) -> None:
    """DynamoDB-typed attributes become plain values or None."""
    assert ddb_value({"key": attribute}, "key") == expected


def test_ddb_value_missing_key() -> None:
    """A missing attribute is None."""
    assert ddb_value({}, "key") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        ("true", True),
        (" TRUE ", True),
        ("1", True),
        ("false", False),
        ("0", False),
        (1, True),
        (0.0, False),
        ("maybe", None),
        (None, None),
        ([], None),
    ],
)
def test_vertical_parsing(raw: Any, expected: bool | None) -> None:
    """The vertical flag accepts the forms the API uses."""
    assert _device(vertical=raw).vertical is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.3, 0.3),
        ("0.45", 0.45),
        (None, None),
        (True, None),
        ("tall", None),
        ({}, None),
    ],
)
def test_height_parsing(raw: Any, expected: float | None) -> None:
    """Tank height must be numeric; anything else is unknown."""
    assert _device(tankHeight=raw).tank_height == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("  Tank  ", "Tank"), ("", None), ([], None), (None, None)]
)
def test_name_parsing(raw: Any, expected: str | None) -> None:
    """Names are trimmed, and empty or non-text names are unknown."""
    assert _device(name=raw).name == expected


def test_device_without_address() -> None:
    """A device without an address cannot be identified and is skipped."""
    assert MopekaDevice.from_api({"name": "Tank"}) is None
    assert MopekaDevice.from_retail({"samples": []}) is None


@pytest.mark.parametrize(
    ("address", "name", "expected"),
    [
        ("aa:bb:cc:dd:6f:01", None, "Mopeka 6F01"),
        ("AABBCCDD7502", None, "Mopeka 7502"),
        ("aa-bb-cc-dd-ee-ff", None, "Mopeka EEFF"),
        ("aa:bb:cc:dd:ee:ff", "Grill", "Grill"),
    ],
)
def test_display_name(address: str, name: str | None, expected: str) -> None:
    """Devices without a name get one from the end of their address."""
    device = MopekaDevice.from_retail({"mac": address, "name": name})
    assert device is not None
    assert device.display_name == expected


@pytest.mark.parametrize(
    ("tank_type", "expected"),
    [
        ("20lb", (20.0, "lb")),
        ("100 gal", (100.0, "gal")),
        ("500L", (500.0, "l")),
        ("12.5 imp_gal", (12.5, "imp_gal")),
        ("custom", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_tank_type(tank_type: str | None, expected: tuple[Any, Any]) -> None:
    """Tank types split into capacity and lower-case unit."""
    assert parse_tank_type(tank_type) == expected


@pytest.mark.parametrize(
    ("level", "height", "vertical", "expected"),
    [
        (0.15, 0.3, True, 50.0),
        (0.0, 0.3, False, 0.0),
        (0.3, 0.3, False, 100.0),
        (0.5, 0.3, True, 100.0),
        (-0.1, 0.3, True, 0.0),
        (0.1, 0.0, True, 0.0),
        (0.45, 0.9, False, 50.0),
    ],
)
def test_fill_percent(
    level: float, height: float, vertical: bool, expected: float
) -> None:
    """Fill is clamped to 0-100 and symmetric for horizontal tanks."""
    assert calculate_fill_percent(level, height, vertical) == expected


def test_horizontal_quarter_is_less_than_quarter_full() -> None:
    """A horizontal tank a quarter of the way up holds less than a quarter."""
    assert 0 < calculate_fill_percent(0.075, 0.3, False) < 25


def test_legacy_reading_offset_and_volume() -> None:
    """Older-platform levels lose 17 mm; volume follows the tank type."""
    reading = MopekaReading.from_api(
        {"Value": {"N": "0.167"}, "Timestamp": {"N": "1762000000000"}}
    )
    tank = MopekaTank(device=_device(), reading=reading)
    assert tank.level_mm == 150.0
    assert tank.fill_percent == 50.0
    assert tank.volume_litres == 9.25
    assert tank.last_reading == datetime(2025, 11, 1, 12, 26, 40, tzinfo=UTC)


@pytest.mark.parametrize(("value", "expected"), [(0.0, 0.0), (0.01, 0.0)])
def test_legacy_level_at_bottom(value: float, expected: float) -> None:
    """Readings at or below the offset are an empty tank, never negative."""
    reading = MopekaReading.from_api({"Value": {"N": str(value)}})
    assert MopekaTank(device=_device(), reading=reading).level_mm == expected


def test_volume_needs_known_capacity() -> None:
    """Without a capacity, volume is unknown even when fill is known."""
    reading = MopekaReading.from_api({"Value": {"N": "0.167"}})
    tank = MopekaTank(device=_device(tankType="custom"), reading=reading)
    assert tank.fill_percent == 50.0
    assert tank.volume_litres is None


def test_unknown_capacity_unit_uses_litres() -> None:
    """An unrecognized capacity unit is treated as litres."""
    reading = MopekaReading.from_api({"Value": {"N": "0.167"}})
    tank = MopekaTank(device=_device(tankType="10bbl"), reading=reading)
    assert tank.volume_litres == 5.0


@pytest.mark.parametrize(
    ("voltage", "expected"),
    [(3.234, 100.0), (2.85, 100.0), (2.525, 50.0), (2.2, 0.0), (1.9, 0.0)],
)
def test_battery_percent(voltage: float, expected: float) -> None:
    """Battery percent maps 2.2-2.85 V to 0-100 %, as the Bluetooth integration does."""
    reading = MopekaReading.from_retail({"v": voltage})
    tank = MopekaTank(device=_device(), reading=reading)
    assert tank.battery_percent == expected


def test_signal_quality_prefers_legacy_quality() -> None:
    """The older platform's quality wins; otherwise the current platform's q."""
    legacy = MopekaReading.from_api({"Quality": {"N": "3"}})
    retail = MopekaReading.from_retail({"q": 251})
    assert MopekaTank(device=_device(), reading=legacy).signal_quality == 3
    assert MopekaTank(device=_device(), reading=retail).signal_quality == 251


def test_tank_without_reading() -> None:
    """A tank with no reading reports every value as unknown."""
    tank = MopekaTank(device=_device())
    assert tank.level_mm is None
    assert tank.fill_percent is None
    assert tank.temperature_c is None
    assert tank.battery_percent is None
    assert tank.last_reading is None
    assert tank.signal_quality is None
