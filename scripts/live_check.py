#!/usr/bin/env python3
"""Check a Mopeka account against the live Mopeka Cloud API.

Uses the integration's own API client, without Home Assistant. Credentials come
from the MOPEKA_EMAIL and MOPEKA_PASSWORD environment variables, or from a
.env file in the repository root (see .env.example). They are never printed.

    uv run --with aiohttp --with pycognito scripts/live_check.py
    uv run --with aiohttp --with pycognito scripts/live_check.py --debug
    uv run --with aiohttp --with pycognito scripts/live_check.py --record tests/fixtures

The summary uses placeholder names, so it is safe to share. --debug adds the
client's request log; tokens and email are redacted, but tank names and sensor
addresses are not. --record writes anonymized API responses for test fixtures.

The test suite never runs this script and never contacts Mopeka.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
from yarl import URL

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "mopeka_ha"

# Import the client modules without running the integration's __init__.py,
# which needs Home Assistant.
_package = types.ModuleType("mopeka_cloud")
_package.__path__ = [str(COMPONENT)]
sys.modules["mopeka_cloud"] = _package
api = importlib.import_module("mopeka_cloud.api")
const = importlib.import_module("mopeka_cloud.const")
models = importlib.import_module("mopeka_cloud.models")

# Device-list keys the integration understands. Values of any other key are
# replaced in recorded fixtures, since their meaning (and privacy) is unknown.
KNOWN_DEVICE_KEYS = {
    "address",
    "name",
    "modelNumber",
    "tankType",
    "tankHeight",
    "vertical",
    "propaneButaneRatio",
}
KNOWN_READING_KEYS = {
    "Value",
    "Timestamp",
    "Temp",
    "BatteryLevel",
    "Quality",
    "Source",
    "ttl",
}


def load_env() -> tuple[str, str]:
    """Return the email and password from the environment or .env."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    email = os.environ.get("MOPEKA_EMAIL", "")
    password = os.environ.get("MOPEKA_PASSWORD", "")
    if not email or not password:
        sys.exit("Set MOPEKA_EMAIL and MOPEKA_PASSWORD (see .env.example).")
    return email, password


def placeholder(value: Any) -> Any:
    """Return a value of the same JSON type that carries no information."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return 0
    if isinstance(value, str):
        return "redacted"
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        # Keep DynamoDB type markers such as {"S": ...} or {"N": ...}.
        return {k: placeholder(v) for k, v in value.items()}
    return None


class Anonymizer:
    """Replace addresses, names, and gateway IDs with stable placeholders."""

    def __init__(self, email: str) -> None:
        self.replacements: dict[str, str] = {email: "user@example.com"}
        self._counts: dict[str, int] = {}
        self.unknown_keys: dict[str, set[str]] = {"device": set(), "reading": set()}

    def _alias(self, real: str, kind: str) -> str:
        if real not in self.replacements:
            count = self._counts[kind] = self._counts.get(kind, 0) + 1
            self.replacements[real] = {
                "address": f"00:00:00:00:00:{count:02X}",
                "name": f"Tank {count}",
                "gateway": f"gateway-{count}",
            }[kind]
        return self.replacements[real]

    def address(self, real: str) -> str:
        return self._alias(real, "address")

    def name(self, real: str) -> str:
        return self._alias(real, "name")

    def device(self, raw: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if key == "address" and isinstance(value, str):
                result[key] = self.address(value)
            elif key == "name" and isinstance(value, str):
                result[key] = self.name(value)
            elif key in KNOWN_DEVICE_KEYS:
                result[key] = value
            else:
                self.unknown_keys["device"].add(key)
                result[key] = placeholder(value)
        return result

    def reading(self, raw: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if key == "Source" and isinstance(value, dict) and "S" in value:
                result[key] = {"S": self._alias(str(value["S"]), "gateway")}
            elif key in KNOWN_READING_KEYS:
                result[key] = value
            else:
                self.unknown_keys["reading"].add(key)
                result[key] = placeholder(value)
        return result

    def text(self, data: Any) -> str:
        """Serialize data, replacing any real values that remain."""
        text = json.dumps(data, indent=2, sort_keys=True)
        for real, alias in sorted(self.replacements.items(), key=lambda i: -len(i[0])):
            text = text.replace(real, alias)
        return text + "\n"


async def record(client: Any, email: str, directory: Path) -> None:
    """Write anonymized device-list and data-shadow responses."""
    anon = Anonymizer(email)
    directory.mkdir(parents=True, exist_ok=True)
    devices = await client._gateway_get(URL(const.GATEWAY_URL), {"_": "0"})
    raw_devices = devices.get("devices", []) if isinstance(devices, dict) else []
    shadows: dict[str, Any] = {}
    for raw in raw_devices:
        address = raw.get("address")
        if not isinstance(address, str):
            continue
        shadow = await client._gateway_get(
            URL(const.GATEWAY_URL) / address / "data-shadow",
            {"limit": "1", "shadowLimit": "1"},
        )
        series = shadow.get("timeSeries", {}) if isinstance(shadow, dict) else {}
        items = series.get("Items", []) if isinstance(series, dict) else []
        scrubbed = {k: placeholder(v) for k, v in shadow.items() if k != "timeSeries"}
        scrubbed["timeSeries"] = {
            **{k: placeholder(v) for k, v in series.items() if k != "Items"},
            "Items": [anon.reading(i) for i in items if isinstance(i, dict)],
        }
        shadows[anon.address(address)] = scrubbed

    top = {k: placeholder(v) for k, v in devices.items() if k != "devices"}
    top["devices"] = [anon.device(d) for d in raw_devices if isinstance(d, dict)]
    (directory / "devices.json").write_text(anon.text(top), encoding="utf-8")
    (directory / "data_shadow.json").write_text(anon.text(shadows), encoding="utf-8")
    print(f"\nRecorded anonymized responses in {directory}/")
    for kind, keys in anon.unknown_keys.items():
        if keys:
            print(f"  Unrecognized {kind} fields (values replaced): {sorted(keys)}")
    print("  Review both files before committing them.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--debug", action="store_true", help="show the request log")
    parser.add_argument("--record", type=Path, help="write anonymized fixtures here")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    email, password = load_env()

    async with aiohttp.ClientSession() as session:
        client = api.MopekaApiClient(session, email, password)
        try:
            data = await client.get_account_data()
        except api.MopekaError as err:
            print(f"FAILED: {type(err).__name__}: {err}")
            return 1

        anon = Anonymizer(email)
        print(f"Signed in. Gateway token type: {client._token_kind}")
        print(
            f"{len(data.devices)} device(s), {len(data.tanks)} with a cloud reading\n"
        )
        now = datetime.now(UTC)
        for address, device in data.devices.items():
            tank = data.tanks.get(address)
            label = anon.name(device.name or address)
            print(
                f"{label}: model={device.model_number} type={device.tank_type} "
                f"height={device.tank_height} vertical={device.vertical}"
            )
            if tank is None or tank.reading is None:
                print("  no cloud reading")
                continue
            reading = tank.reading
            age = (
                f"{(now - reading.timestamp).total_seconds() / 60:.0f} min ago"
                if reading.timestamp
                else "unknown"
            )
            print(
                f"  reading {age}: level={reading.level_m} m "
                f"temp={reading.temperature_c} C battery={reading.battery_voltage} V "
                f"quality={reading.quality} fill={tank.fill_percent}%"
            )

        if args.record:
            await record(client, email, args.record)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
