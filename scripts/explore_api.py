#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp", "pycognito"]
# ///
"""Print the shape of Mopeka's current cloud API responses for your account.

One-off research tool. Signs in to Mopeka's current Cognito user pool with the
integration's SRP code, calls a fixed list of read-only routes used by Mopeka's
own web app, and prints each response with personal data removed:

- strings become "<str len N>", except a short allowlist of harmless values
  (units, styles, models, flags);
- MAC addresses, emails, coordinates, phone numbers, and postcodes are blanked;
- numbers such as readings and timestamps are kept.

Credentials come from MOPEKA_EMAIL and MOPEKA_PASSWORD, or a .env file in the
repository root. Review the output before sharing it.

    uv run scripts/explore_api.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
_package = types.ModuleType("mopeka_cloud")
_package.__path__ = [str(ROOT / "custom_components" / "mopeka_ha")]
sys.modules["mopeka_cloud"] = _package
api = importlib.import_module("mopeka_cloud.api")

# Mopeka's current user pool and web client, from the public config of
# www.mopeka.cloud. The phone app reads data from dev.mopeka.cloud.
api.COGNITO_USER_POOL_ID = "us-east-1_CdV73bmzB"
api.COGNITO_CLIENT_ID = "3laqnk4f8p1i29895clng4jh5g"
HOSTS = ("https://dev.mopeka.cloud", "https://www.mopeka.cloud")

# Read-only routes. {org}, {device}, and {mac} are filled from the device list.
ACCOUNT_ROUTES = (
    "/api/devices",
    "/api/devices/list",
    "/api/devices/names",
    "/api/tanktypes",
    "/api/organizations/names",
)
DEVICE_ROUTES = (
    "/api/devices/{org}/{device}/shadow",
    "/api/devices/getShadowFromMac?mac={mac}",
    "/api/devices/{org}/{device}/samples?limit=2&attributesToGet=t,r,q,v,c,s&qualityType=1",
)
MAX_DEVICES = 3

_KEEP_STRING_KEYS = re.compile(
    r"(unit|units|abbreviation|style|model|type|kind|status|state|version|"
    r"orientation|category|format|product|hardware|firmware|role)$",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = re.compile(
    r"(lat|lng|lon|latitude|longitude|phone|zip|postal|postcode|address|street|"
    r"city|email|owner|customer|contact|serial|esn|imei|iccid|token|secret)",
    re.IGNORECASE,
)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]?){5,7}[0-9A-Fa-f]{2}$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def shape(value: Any, key: str = "") -> Any:
    """Return value with personal data removed but structure and numbers kept."""
    if isinstance(value, dict):
        return {k: shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [shape(v, key) for v in value[:3]]
        return items + ([f"... {len(value) - 3} more"] if len(value) > 3 else [])
    if key and _SENSITIVE_KEYS.search(key) and not isinstance(value, bool):
        return f"<{type(value).__name__} removed>"
    if isinstance(value, str):
        if _MAC_RE.match(value) or _EMAIL_RE.search(value):
            return "<removed>"
        if key and _KEEP_STRING_KEYS.search(key) and len(value) <= 40:
            return value
        return f"<str len {len(value)}>"
    return value


def find(obj: Any, *names: str) -> Any:
    """Return the first value for any of the keys, searching nested dicts."""
    if isinstance(obj, dict):
        for name in names:
            if obj.get(name) not in (None, ""):
                return obj[name]
        for value in obj.values():
            if isinstance(value, dict) and (found := find(value, *names)) is not None:
                return found
    return None


def device_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    if isinstance(payload, dict):
        for key in ("devices", "items", "data", "results", "Items"):
            if isinstance(payload.get(key), list):
                return [d for d in payload[key] if isinstance(d, dict)]
    return []


async def get(session: aiohttp.ClientSession, host: str, path: str, token: str) -> Any:
    async with session.get(
        host + path, headers={"token": token, "Accept": "application/json"}
    ) as resp:
        text = await resp.text()
        try:
            body: Any = json.loads(text)
        except ValueError:
            body = f"<non-JSON, {len(text)} characters>"
        return resp.status, body


async def main() -> int:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    email, password = os.environ.get("MOPEKA_EMAIL"), os.environ.get("MOPEKA_PASSWORD")
    if not email or not password:
        sys.exit("Set MOPEKA_EMAIL and MOPEKA_PASSWORD (see .env.example).")

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        client = api.MopekaApiClient(session, email, password)
        try:
            await client.authenticate()
        except api.MopekaError as err:
            print(f"Sign-in to the current pool FAILED: {type(err).__name__}: {err}")
            return 1
        token = client._tokens.access_token
        print("Signed in to the current Mopeka user pool.\n")

        for host in HOSTS:
            print(f"######## {host}")
            devices: list[dict[str, Any]] = []
            for path in ACCOUNT_ROUTES:
                status, body = await get(session, host, path, token)
                print(f"=== GET {path} -> HTTP {status}")
                print(json.dumps(shape(body), indent=1)[:3000])
                devices = devices or device_list(body)
            for index, device in enumerate(devices[:MAX_DEVICES], start=1):
                ids = {
                    "org": find(device, "organizationId", "orgId", "organization_id"),
                    "device": find(device, "deviceId", "id", "device_id", "_id"),
                    "mac": find(device, "fullMac", "mac", "macAddress"),
                }
                print(
                    f"--- device {index}: found keys "
                    f"{ {k: v is not None for k, v in ids.items()} }"
                )
                for template in DEVICE_ROUTES:
                    if any(
                        f"{{{k}}}" in template and v is None for k, v in ids.items()
                    ):
                        continue
                    path = template.format(
                        **{k: v for k, v in ids.items() if v is not None}
                    )
                    status, body = await get(session, host, path, token)
                    label = re.sub(
                        r"(/api/devices/)[^/?]+/[^/?]+", r"\1{org}/{device}", path
                    )
                    label = re.sub(r"mac=[^&]+", "mac={mac}", label)
                    print(f"=== GET {label} -> HTTP {status}")
                    print(json.dumps(shape(body), indent=1)[:3000])
            if devices:
                break
            print("(no devices from this host; trying the next one)\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
