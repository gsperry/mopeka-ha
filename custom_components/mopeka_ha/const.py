"""Constants for the Mopeka HA integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "mopeka_ha"

# Config entry options. CONF_UPDATE_INTERVAL and CONF_SENSORS keep the keys used
# by releases before the fork so existing entries keep their settings.
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_SENSORS: Final = "sensors"
CONF_EXCLUDED_DEVICES: Final = "excluded_devices"

# Poll interval, in minutes.
DEFAULT_UPDATE_INTERVAL: Final = 5
MIN_UPDATE_INTERVAL: Final = 1
MAX_UPDATE_INTERVAL: Final = 60

# Amazon Cognito user pool and app client used by the Tank Check mobile app.
COGNITO_REGION: Final = "us-east-1"
COGNITO_USER_POOL_ID: Final = "us-east-1_sLQ1KlStp"
COGNITO_CLIENT_ID: Final = "7dafulgmkck7u9hiju6v6p1emt"
COGNITO_URL: Final = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

GATEWAY_URL: Final = "https://gateway.mopeka.cloud/app/sensors"

# Headers the mobile app sends to the gateway. The app is a web view, so the
# gateway sees a mobile Safari user agent and an app:// origin.
GATEWAY_HEADERS: Final = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "app://localhost",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15"
    ),
}

REQUEST_TIMEOUT: Final = 20  # seconds
