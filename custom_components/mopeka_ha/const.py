"""Constants for the Mopeka HA integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

DOMAIN: Final = "mopeka_ha"

# Config entry version. Minor version 2 replaced the per-measurement options of
# releases before 2.0 with fixed entity descriptions.
CONFIG_ENTRY_VERSION: Final = 1
CONFIG_ENTRY_MINOR_VERSION: Final = 2

# Config entry data. CONF_BACKEND records which Mopeka platform holds the
# account, so later sign-ins go straight to it.
CONF_BACKEND: Final = "backend"
BACKEND_RETAIL: Final = "retail"
BACKEND_LEGACY: Final = "legacy"

# Config entry options. CONF_UPDATE_INTERVAL and CONF_SENSORS keep the keys used
# by releases before the fork so existing entries keep their settings.
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_SENSORS: Final = "sensors"
CONF_EXCLUDED_DEVICES: Final = "excluded_devices"

# Poll interval, in minutes.
DEFAULT_UPDATE_INTERVAL: Final = 5
MIN_UPDATE_INTERVAL: Final = 1
MAX_UPDATE_INTERVAL: Final = 60


@dataclass(frozen=True, slots=True)
class CognitoPool:
    """An Amazon Cognito user pool and the app client used to sign in to it."""

    pool_id: str
    client_id: str

    @property
    def region(self) -> str:
        """Return the AWS region, which prefixes the pool ID."""
        return self.pool_id.split("_", 1)[0]

    @property
    def url(self) -> str:
        """Return the Cognito Identity Provider endpoint for the region."""
        return f"https://cognito-idp.{self.region}.amazonaws.com/"


# Current platform: the user pool behind mopeka.cloud, signed in to with the
# Mopeka Co-Pilot app's client ID. Accounts created for Co-Pilot live here.
CURRENT_POOL: Final = CognitoPool("us-east-1_CdV73bmzB", "5dbn0o9pcqli659c1t3ptjpqmt")

# Older platform used by the Tank Check app, with its own pool and gateway.
LEGACY_POOL: Final = CognitoPool("us-east-1_sLQ1KlStp", "7dafulgmkck7u9hiju6v6p1emt")

COGNITO_URL: Final = CURRENT_POOL.url  # Both pools are in us-east-1.

# Current platform's data route for retail (consumer) accounts, as called by
# Co-Pilot. The request body must always be EMPTY_RETAIL_SYNC: any entries in it
# would add, change, or delete devices on the account.
RETAIL_SYNC_URL: Final = "https://dev.mopeka.cloud/api/devices/retail"
EMPTY_RETAIL_SYNC: Final[dict[str, list[Any]]] = {"devices": [], "deleted": []}
RETAIL_HEADERS: Final = {
    "Accept": "application/json",
    "referrer": "copilot-2.9.14-1.0.0",
    "User-Agent": "CoPilot/1.0.0 CFNetwork/3896.200.61 Darwin/27.2.0",
}

# Older platform's gateway.
GATEWAY_URL: Final = "https://gateway.mopeka.cloud/app/sensors"

# Headers the Tank Check app sends to the older gateway. The app is a web view,
# so the gateway sees a mobile Safari user agent and an app:// origin.
GATEWAY_HEADERS: Final = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "app://localhost",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15"
    ),
}

REQUEST_TIMEOUT: Final = 20  # seconds
