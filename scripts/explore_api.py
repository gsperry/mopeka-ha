#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp", "pycognito"]
# ///
"""Check how Mopeka's current cloud platform serves data to your account.

One-off research tool. Signs in to Mopeka's current Cognito user pool with the
integration's SRP code, then:

1. prints the account's role and Cognito groups, from your own user profile
   and token claims (not the tokens themselves);
2. checks whether the older data gateway, gateway.mopeka.cloud, accepts the
   current pool's access or ID token, and if so prints the shape of the device
   list and one reading.

Output has personal data removed: strings become "<str len N>" except a short
allowlist of harmless values; MAC addresses, emails, coordinates, phone numbers,
and postcodes are blanked; numbers (including numeric strings) are kept.

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
const = importlib.import_module("mopeka_cloud.const")

# Mopeka's current user pool and web client, from the public config of
# www.mopeka.cloud.
api.COGNITO_USER_POOL_ID = "us-east-1_CdV73bmzB"
api.COGNITO_CLIENT_ID = "3laqnk4f8p1i29895clng4jh5g"
PORTAL = "https://dev.mopeka.cloud"

_KEEP_STRING_KEYS = re.compile(
    r"(unit|units|abbreviation|style|model|modelNumber|type|tankType|kind|status|"
    r"state|version|orientation|category|format|product|hardware|firmware|role|"
    r"token_use|scope)$",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = re.compile(
    r"(lat|lng|lon|latitude|longitude|phone|zip|postal|postcode|address|street|"
    r"city|email|owner|customer|contact|serial|esn|imei|iccid|token$|secret)",
    re.IGNORECASE,
)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]?){5,7}[0-9A-Fa-f]{2}$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


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
        tokens = client._tokens
        print("Signed in to the current Mopeka user pool.\n")

        # 1. Role and groups.
        access, ident = claims(tokens.access_token), claims(tokens.id_token)
        print("Access token claim names:", sorted(access))
        print("ID token claim names:", sorted(ident))
        print(
            "cognito:groups:", access.get("cognito:groups", ident.get("cognito:groups"))
        )
        custom = {k: v for k, v in ident.items() if k.startswith("custom:")}
        print("ID token custom claims:", shape(custom))
        username = access.get("username") or ident.get("cognito:username")
        if username:
            status, body = await get(
                session,
                f"{PORTAL}/api/users/{username}",
                {"token": tokens.access_token},
            )
            show("GET portal /api/users/{username}", status, body)

        # 2. The older gateway with the current pool's tokens.
        print()
        working: str | None = None
        devices: list[dict[str, Any]] = []
        for kind in ("access", "id"):
            headers = {**const.GATEWAY_HEADERS, "Auth": tokens.get(kind)}
            status, body = await get(
                session, const.GATEWAY_URL, headers, _=str(int(time.time() * 1000))
            )
            show(f"GET gateway /app/sensors with the {kind} token", status, body)
            if status == 200 and isinstance(body, dict):
                working = kind
                devices = [d for d in body.get("devices", []) if isinstance(d, dict)]
                break

        if working is None:
            print("\nThe older gateway does not accept the current pool's tokens.")
            return 0
        headers = {**const.GATEWAY_HEADERS, "Auth": tokens.get(working)}
        for index, device in enumerate(devices[:2], start=1):
            address = device.get("address")
            if not isinstance(address, str):
                continue
            status, body = await get(
                session,
                f"{const.GATEWAY_URL}/{address}/data-shadow",
                headers,
                limit="1",
                shadowLimit="1",
            )
            show(
                f"GET gateway /app/sensors/{{device {index}}}/data-shadow", status, body
            )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
