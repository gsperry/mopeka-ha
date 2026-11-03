#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp", "pycognito"]
# ///
"""Check how Mopeka's current cloud platform serves data to your account.

One-off research tool. Signs in to Mopeka's current Cognito user pool as the
Co-Pilot app does (same app client ID), then calls the read-only device routes
on dev.mopeka.cloud and prints the shape of each response.

Output has personal data removed: strings become "<str len N>" except a short
allowlist of harmless values; MAC addresses, emails, names, coordinates, phone
numbers, and postcodes are blanked; numbers (including numeric strings) are kept.

Credentials come from MOPEKA_EMAIL and MOPEKA_PASSWORD, or a .env file in the
repository root. Review the output before sharing it.

    uv run scripts/explore_api.py

In the Home Assistant OS terminal add-on, use a Python that uv downloads:

    uv run --managed-python --python 3.13 scripts/explore_api.py
"""

from __future__ import annotations

import asyncio
import base64
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

# Mopeka's current user pool (from the public config of www.mopeka.cloud) and
# the Co-Pilot app's client ID (from a capture of the app signing in).
api.COGNITO_USER_POOL_ID = "us-east-1_CdV73bmzB"
api.COGNITO_CLIENT_ID = "5dbn0o9pcqli659c1t3ptjpqmt"
PORTAL = "https://dev.mopeka.cloud"
ACCOUNT_ROUTES = ("/api/devices", "/api/devices/list", "/api/devices/names")
DEVICE_ROUTES = (
    "/api/devices/{org}/{device}",
    "/api/devices/{org}/{device}/shadow",
    "/api/devices/getShadowFromMac?mac={mac}",
    "/api/devices/{org}/{device}/samples?limit=2&attributesToGet=t,r,q,v,c,s&qualityType=1",
)
MAX_DEVICES = 2

_KEEP_STRING_KEYS = re.compile(
    r"(unit|units|abbreviation|style|model|modelNumber|type|tankType|kind|status|"
    r"state|version|orientation|category|format|product|hardware|firmware|role|"
    r"token_use|scope)$",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = re.compile(
    r"(lat|lng|lon|latitude|longitude|coordinates|geo|location|point|phone|zip|"
    r"postal|postcode|address|street|city|email|owner|customer|contact|serial|esn|"
    r"imei|iccid|token$|secret|name$)",
    re.IGNORECASE,
)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]?){5,7}[0-9A-Fa-f]{2}$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


def shape(value: Any, key: str = "") -> Any:
    """Return value with personal data removed but structure and numbers kept."""
    # Check the key first, so sensitive objects and lists are removed whole.
    if key and _SENSITIVE_KEYS.search(key) and not isinstance(value, bool | None):
        return f"<{type(value).__name__} removed>"
    if isinstance(value, dict):
        return {k: shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [shape(v, key) for v in value[:3]]
        return items + ([f"... {len(value) - 3} more"] if len(value) > 3 else [])
    if isinstance(value, str):
        if _MAC_RE.match(value) or _EMAIL_RE.search(value):
            return "<removed>"
        if _NUMBER_RE.match(value) and len(value) <= 20:
            return value
        if key and _KEEP_STRING_KEYS.search(key) and len(value) <= 40:
            return value
        return f"<str len {len(value)}>"
    return value


def claims(jwt: str) -> dict[str, Any]:
    """Decode a JWT's payload without verifying it."""
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def show(label: str, status: int, body: Any) -> None:
    print(f"=== {label} -> HTTP {status}")
    print(json.dumps(shape(body), indent=1)[:4000])


async def get(
    session: aiohttp.ClientSession, url: str, headers: dict[str, str], **params: str
) -> tuple[int, Any]:
    async with session.get(url, headers=headers, params=params or None) as resp:
        text = await resp.text()
        try:
            return resp.status, json.loads(text)
        except ValueError:
            return resp.status, f"<non-JSON, {len(text)} characters>"


def device_list(payload: Any) -> list[dict[str, Any]]:
    """Return device objects from a device-route response."""
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    if isinstance(payload, dict):
        for key in ("devices", "items", "data", "results", "rows"):
            if isinstance(payload.get(key), list):
                return [d for d in payload[key] if isinstance(d, dict)]
    return []


def first(obj: dict[str, Any], *names: str) -> Any:
    for name in names:
        if obj.get(name) not in (None, ""):
            return obj[name]
    return None


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
            print(
                f"Sign-in with the Co-Pilot client FAILED: {type(err).__name__}: {err}"
            )
            return 1
        tokens = client._tokens
        print("Signed in with the Co-Pilot app client.")
        print(
            "Access token client_id matches:",
            claims(tokens.access_token).get("client_id") == api.COGNITO_CLIENT_ID,
            "\n",
        )

        headers = {"token": tokens.access_token, "Accept": "application/json"}
        devices: list[dict[str, Any]] = []
        for path in ACCOUNT_ROUTES:
            status, body = await get(session, PORTAL + path, headers)
            show(f"GET {path}", status, body)
            devices = devices or device_list(body)

        if not devices:
            print("\nNo device list. Trying the ID token in the same header once:")
            status, body = await get(
                session,
                PORTAL + "/api/devices",
                {"token": tokens.id_token, "Accept": "application/json"},
            )
            show("GET /api/devices (ID token)", status, body)
            return 0

        for index, device in enumerate(devices[:MAX_DEVICES], start=1):
            ids = {
                "org": first(device, "organizationId", "orgId"),
                "device": first(device, "id", "deviceId"),
                "mac": first(device, "fullMac", "mac"),
            }
            print(
                f"\n--- device {index}: has {[k for k, v in ids.items() if v is not None]}"
            )
            for template in DEVICE_ROUTES:
                needed = re.findall(r"{(\w+)}", template)
                if any(ids.get(n) is None for n in needed):
                    continue
                status, body = await get(
                    session, PORTAL + template.format(**ids), headers
                )
                show(f"GET {template}", status, body)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
