#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp", "pycognito"]
# ///
"""Check how Mopeka's current cloud platform serves data to your account.

One-off research tool. Signs in to Mopeka's current Cognito user pool as the
Co-Pilot app does (same app client ID), then calls the app's retail sync route,
PUT /api/devices/retail, and prints the shape of each response. The request
body is always {"devices": [], "deleted": []}, exactly as the app sends when it
has no local changes, so nothing on the account is modified.

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
import time
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
# Headers the Co-Pilot app sends, minus its crash-reporting headers.
APP_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "referrer": "copilot-2.9.14-1.0.0",
    "User-Agent": "CoPilot/1.0.0 CFNetwork/3896.200.61 Darwin/27.2.0",
}
# Never send anything but empty lists: entries here would change the account.
EMPTY_SYNC = {"devices": [], "deleted": []}

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


async def sync(
    session: aiohttp.ClientSession, token: str, since_ms: int
) -> tuple[int, Any]:
    """Call the retail sync route for changes since since_ms."""
    async with session.put(
        f"{PORTAL}/api/devices/retail",
        params={"lastUpdatedAt": str(since_ms)},
        headers={**APP_HEADERS, "token": token},
        json=EMPTY_SYNC,
    ) as resp:
        text = await resp.text()
        try:
            return resp.status, json.loads(text)
        except ValueError:
            return resp.status, f"<non-JSON, {len(text)} characters>"


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
        token = client._tokens.access_token
        print("Signed in with the Co-Pilot app client.\n")

        now_ms = int(time.time() * 1000)
        for label, since in (
            ("everything (lastUpdatedAt=0)", 0),
            ("last 24 hours", now_ms - 24 * 3600 * 1000),
        ):
            status, body = await sync(session, token, since)
            show(f"PUT /api/devices/retail, {label}", status, body)
            if isinstance(body, dict):
                for device in body.get("devices", [])[:2]:
                    if isinstance(device, dict):
                        samples = device.get("samples")
                        count = len(samples) if isinstance(samples, list) else None
                        print(f"  device keys: {sorted(device)}; samples: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
