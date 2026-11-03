"""Tests for the current Mopeka platform: Co-Pilot sign-in and retail sync."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.mopeka_ha.api import (
    MopekaApiClient,
    MopekaApiError,
    MopekaAuthError,
    MopekaConnectionError,
    MopekaError,
)
from custom_components.mopeka_ha.const import (
    BACKEND_LEGACY,
    BACKEND_RETAIL,
    CONF_BACKEND,
    CURRENT_POOL,
    DOMAIN,
    EMPTY_RETAIL_SYNC,
)
from custom_components.mopeka_ha.models import raw_level_to_m

from .fake_cloud import FakeCloud, install_fake_cloud, make_sample

EMAIL = "retail@example.com"
PASSWORD = "co-pilot-password"
MAC_A = "aa:bb:cc:dd:6f:01"
MAC_B = "aa:bb:cc:dd:75:02"
MAC_IDLE = "aa:bb:cc:dd:00:03"


@pytest.fixture
def cloud(aioclient_mock: AiohttpClientMocker) -> FakeCloud:
    """Return a fake cloud with a Co-Pilot account holding two tanks.

    Readings match a real account's scrubbed sync response. A third device has
    never reported.
    """
    cloud = install_fake_cloud(aioclient_mock)
    cloud.current.add_user(EMAIL, PASSWORD)
    cloud.retail.add_device(MAC_A, make_sample(MAC_A))
    cloud.retail.add_device(
        MAC_B,
        make_sample(
            MAC_B,
            raw_level=1068,
            temp_c=8,
            battery=3.25,
            quality=277,
            timestamp_s=1_793_669_405,
        ),
    )
    cloud.retail.add_device(MAC_IDLE)
    return cloud


def _client(
    hass: HomeAssistant, backend: str | None = None, **kw: str
) -> MopekaApiClient:
    return MopekaApiClient(
        async_get_clientsession(hass),
        kw.get("email", EMAIL),
        kw.get("password", PASSWORD),
        backend,
    )


def _entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, **data},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    return entry


# Level conversion


@pytest.mark.parametrize(
    ("raw", "temp_c", "expected_mm"),
    [(890, 6, 384.4), (1068, 8, 454.2), (0, 20, 0.0), (1000, 25, 367.0)],
)
def test_raw_level_to_m(raw: float, temp_c: float, expected_mm: float) -> None:
    """Raw echo time converts with the propane coefficients and raw temperature."""
    assert raw_level_to_m(raw, temp_c) * 1000 == pytest.approx(expected_mm, abs=0.1)


# Sign-in and sync


async def test_detects_current_platform(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A Co-Pilot account signs in with Co-Pilot's client and reads both tanks."""
    client = _client(hass)
    data = await client.get_account_data()

    assert client.backend == BACKEND_RETAIL
    assert cloud.current.count("RespondToAuthChallenge") == 1
    assert cloud.cognito.requests == []
    assert cloud.gateway.requests == []
    initiate = next(b for t, b in cloud.current.requests if t == "InitiateAuth")
    assert initiate["ClientId"] == CURRENT_POOL.client_id

    assert set(data.devices) == {MAC_A, MAC_B, MAC_IDLE}
    assert set(data.tanks) == {MAC_A, MAC_B}


async def test_sync_request_matches_the_app(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """The sync is a PUT with an empty body, the access token, and app headers."""
    await _client(hass).get_account_data()

    (request,) = cloud.retail.requests
    assert request["method"] == "PUT"
    assert request["body"] == EMPTY_RETAIL_SYNC
    assert request["query"] == {"lastUpdatedAt": "0"}
    assert request["headers"]["token"] in cloud.current.access_tokens
    assert request["headers"]["referrer"].startswith("copilot-")
    assert "CoPilot" in request["headers"]["User-Agent"]


async def test_parsed_values(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Samples become readings with converted level, temperature, and battery."""
    data = await _client(hass).get_account_data()
    tank = data.tanks[MAC_A]

    assert tank.device.display_name == "Mopeka 6F01"
    assert tank.last_reading is not None
    assert tank.last_reading.timestamp() == 1_793_667_625
    assert tank.level_mm == 384.4
    assert tank.temperature_c == 6
    assert tank.battery_voltage == 3.234
    assert tank.battery_percent == 100.0
    assert tank.signal_quality == 251
    # The cloud has no tank settings, so these are unknown.
    assert tank.fill_percent is None
    assert tank.volume_litres is None
    assert data.tanks[MAC_B].level_mm == 454.2


async def test_latest_sample_wins(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """With several samples, the newest is used whatever the order."""
    cloud.retail.devices[0]["samples"] = [
        make_sample(MAC_A, raw_level=100, timestamp_s=1_793_000_000),
        make_sample(MAC_A, raw_level=900, timestamp_s=1_793_600_000),
        make_sample(MAC_A, raw_level=500, timestamp_s=1_793_300_000),
        "junk",
    ]
    data = await _client(hass).get_account_data()
    reading = data.tanks[MAC_A].reading
    assert reading is not None and reading.timestamp is not None
    assert reading.timestamp.timestamp() == 1_793_600_000


async def test_excluded_and_malformed_devices(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Excluded devices are listed but not tanks; entries without a MAC are skipped."""
    cloud.retail.devices.append({"samples": [make_sample("x")]})
    cloud.retail.devices.append("junk")
    data = await _client(hass).get_account_data(frozenset({MAC_B}))
    assert set(data.devices) == {MAC_A, MAC_B, MAC_IDLE}
    assert set(data.tanks) == {MAC_A}


async def test_sample_with_missing_fields(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A sample without temperature or level yields unknown values, not errors."""
    cloud.retail.devices[0]["samples"] = [{"m": MAC_A, "t": "soon", "v": None}]
    data = await _client(hass).get_account_data()
    tank = data.tanks[MAC_A]
    assert tank.reading is not None
    assert tank.last_reading is None
    assert tank.level_mm is None
    assert tank.battery_percent is None
    assert tank.signal_quality is None


async def test_tokens_reused_and_refreshed(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """Polls reuse the token; a 401 refreshes it once and retries."""
    client = _client(hass)
    await client.get_account_data()
    await client.get_account_data()
    assert cloud.current.count("RespondToAuthChallenge") == 1

    cloud.current.expire_all_tokens()
    data = await client.get_account_data()
    assert len(data.tanks) == 2
    assert cloud.current.count("InitiateAuth", "REFRESH_TOKEN_AUTH") == 1


async def test_known_backend_skips_detection(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A stored backend signs in to that platform only."""
    await _client(hass, BACKEND_RETAIL).get_account_data()
    assert cloud.cognito.requests == []

    cloud.cognito.add_user("old@example.com", "old-password")
    client = _client(
        hass, BACKEND_LEGACY, email="old@example.com", password="old-password"
    )
    await client.authenticate()
    assert cloud.current.count("RespondToAuthChallenge") == 1  # only the first client


async def test_older_platform_account_falls_back(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An account only on the older platform is detected after the current one says no."""
    cloud.cognito.add_user("old@example.com", "old-password")
    client = _client(hass, email="old@example.com", password="old-password")
    await client.authenticate()
    assert client.backend == BACKEND_LEGACY
    assert cloud.current.count("RespondToAuthChallenge") == 1
    assert cloud.cognito.count("RespondToAuthChallenge") == 1


async def test_wrong_password(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A wrong password is reported as the current platform's rejection."""
    client = _client(hass, password="wrong")
    with pytest.raises(MopekaAuthError) as err:
        await client.get_account_data()
    assert err.value.code == "NotAuthorizedException"
    assert client.backend is None
    assert cloud.retail.requests == []


async def test_mfa_on_current_platform_is_not_retried_elsewhere(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """A sign-in step on the current platform is reported, not hidden by fallback."""
    cloud.current.extra_challenge = "SOFTWARE_TOKEN_MFA"
    with pytest.raises(MopekaAuthError) as err:
        await _client(hass).authenticate()
    assert err.value.code == "SOFTWARE_TOKEN_MFA"
    assert cloud.cognito.requests == []


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ((HTTPStatus.FORBIDDEN, {"message": "Not authorized"}), MopekaApiError),
        (
            (HTTPStatus.INTERNAL_SERVER_ERROR, {"message": "boom"}),
            MopekaConnectionError,
        ),
        ((HTTPStatus.TOO_MANY_REQUESTS, {"message": "slow"}), MopekaConnectionError),
        ((HTTPStatus.NOT_FOUND, {"message": "gone"}), MopekaApiError),
        ((HTTPStatus.OK, ["not", "an", "object"]), MopekaApiError),
        ((HTTPStatus.OK, {"devicesList": []}), MopekaApiError),
    ],
)
async def test_sync_errors(
    hass: HomeAssistant,
    cloud: FakeCloud,
    response: tuple[int, Any],
    expected: type[MopekaError],
) -> None:
    """Sync failures map to the right exception."""
    client = _client(hass)
    await client.authenticate()
    cloud.retail.queued.append(cloud.retail.response(*response))
    with pytest.raises(expected):
        await client.get_account_data()


async def test_sync_non_json(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A non-JSON page is an API error."""
    cloud.retail.queued.append(cloud.retail.response(HTTPStatus.OK, text="<html>"))
    with pytest.raises(MopekaApiError):
        await _client(hass).get_account_data()


async def test_sync_rejects_fresh_token(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """If a refreshed token is also rejected, the error says so."""
    unauthorized = (HTTPStatus.UNAUTHORIZED, {"message": "Unauthorized"})
    cloud.retail.queued += [
        cloud.retail.response(*unauthorized),
        cloud.retail.response(*unauthorized),
    ]
    with pytest.raises(MopekaApiError, match="newly issued"):
        await _client(hass).get_account_data()


@pytest.mark.parametrize(
    "exc", [aiohttp.ClientConnectionError("reset"), TimeoutError()]
)
async def test_sync_unreachable(
    hass: HomeAssistant, cloud: FakeCloud, exc: Exception
) -> None:
    """Network failures are connection errors."""
    cloud.retail.queued.append(exc)
    with pytest.raises(MopekaConnectionError):
        await _client(hass).get_account_data()


async def test_sync_debug_log_is_redacted(
    hass: HomeAssistant, cloud: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    """The sync is logged with its status, without tokens or the email."""
    caplog.set_level(logging.DEBUG, logger="custom_components.mopeka_ha")
    await _client(hass).get_account_data()
    assert "Retail sync PUT https://dev.mopeka.cloud/api/devices/retail" in caplog.text
    assert "HTTP 200" in caplog.text
    assert "Account is on the retail platform" in caplog.text
    assert EMAIL not in caplog.text and PASSWORD not in caplog.text
    for token in cloud.current.access_tokens | cloud.current.refresh_tokens:
        assert token not in caplog.text


# Home Assistant


async def test_config_flow(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Setup stores the platform and creates the tanks' sensors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND] == BACKEND_RETAIL
    assert hass.states.get("sensor.mopeka_6f01_level").state == "384.4"
    assert hass.states.get("sensor.mopeka_7502_level").state == "454.2"
    assert hass.states.get("sensor.mopeka_6f01_temperature").state == "6.0"
    # No tank sizes in the cloud, so no fill or volume sensors.
    assert hass.states.get("sensor.mopeka_6f01_fill_level") is None
    assert hass.states.get("sensor.mopeka_6f01_volume") is None


async def test_existing_entry_learns_backend(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    """An entry created before detection stores its platform at setup."""
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_BACKEND] == BACKEND_RETAIL


async def test_tank_drops_out(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A tank whose samples disappear goes unavailable, then recovers."""
    entry = _entry(hass, **{CONF_BACKEND: BACKEND_RETAIL})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    samples = cloud.retail.devices[1]["samples"]

    cloud.retail.devices[1]["samples"] = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.mopeka_7502_level").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.mopeka_6f01_level").state == "384.4"

    cloud.retail.devices[1]["samples"] = samples
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.mopeka_7502_level").state == "454.2"
