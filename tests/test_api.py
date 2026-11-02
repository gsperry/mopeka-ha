"""Tests for the Mopeka Cloud API client."""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import Any

import aiohttp
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.mopeka_ha.api import (
    REDACTED,
    MopekaApiClient,
    MopekaApiError,
    MopekaAuthError,
    MopekaConnectionError,
    MopekaError,
    _loggable_body,
    redact,
)
from custom_components.mopeka_ha.const import COGNITO_URL, GATEWAY_URL

from .conftest import ADDRESS_A, ADDRESS_B, EMAIL, PASSWORD
from .fake_cloud import FakeCloud, make_device


def _client(
    hass: HomeAssistant, email: str = EMAIL, password: str = PASSWORD
) -> MopekaApiClient:
    return MopekaApiClient(async_get_clientsession(hass), email, password)


def _cognito_response(status: int, payload: Any) -> AiohttpClientMockResponse:
    return AiohttpClientMockResponse(
        "post", URL(COGNITO_URL), status=status, json=payload
    )


def _gateway_response(
    status: int, *, json: Any = None, text: str | None = None
) -> AiohttpClientMockResponse:
    return AiohttpClientMockResponse(
        "get", URL(GATEWAY_URL), status=status, json=json, text=text
    )


# Sign-in


async def test_sign_in_and_read_account(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """A correct password proof yields tokens, and the gateway accepts them."""
    data = await api_client.get_account_data()

    assert set(data.devices) == {ADDRESS_A, ADDRESS_B}
    assert set(data.tanks) == {ADDRESS_A, ADDRESS_B}
    assert data.tanks[ADDRESS_A].device.name == "Tank A"
    assert data.tanks[ADDRESS_A].reading is not None
    assert data.tanks[ADDRESS_A].reading.gateway_id == "gw-0001"

    assert fake_cloud.cognito.count("InitiateAuth", "USER_SRP_AUTH") == 1
    assert fake_cloud.cognito.count("RespondToAuthChallenge") == 1
    # Cognito's internal user ID, not the email, is used for the SRP proof.
    respond = next(
        b for t, b in fake_cloud.cognito.requests if t == "RespondToAuthChallenge"
    )
    assert (
        respond["ChallengeResponses"]["USERNAME"]
        == fake_cloud.cognito.users[EMAIL].user_id
    )
    assert respond["Session"]

    for _, headers in fake_cloud.gateway.requests:
        assert headers["Auth"] in fake_cloud.cognito.access_tokens
        assert headers["Origin"] == "app://localhost"
        assert "iPhone" in headers["User-Agent"]


async def test_authenticate_only_signs_in(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """authenticate() signs in without calling the gateway."""
    await api_client.authenticate()
    assert fake_cloud.cognito.count("RespondToAuthChallenge") == 1
    assert fake_cloud.gateway.requests == []


async def test_wrong_password(hass: HomeAssistant, fake_cloud: FakeCloud) -> None:
    """A wrong password is an authentication error, not a connection error."""
    with pytest.raises(MopekaAuthError) as err:
        await _client(hass, password="wrong").get_account_data()
    assert err.value.code == "NotAuthorizedException"
    assert fake_cloud.gateway.requests == []


async def test_unknown_user(hass: HomeAssistant, fake_cloud: FakeCloud) -> None:
    """An unknown lower-case email is tried once and rejected."""
    with pytest.raises(MopekaAuthError) as err:
        await _client(hass, email="nobody@example.com").authenticate()
    assert err.value.code == "UserNotFoundException"
    assert fake_cloud.cognito.count("InitiateAuth") == 1


async def test_email_case_and_whitespace(
    hass: HomeAssistant, fake_cloud: FakeCloud
) -> None:
    """An email entered with capitals and spaces falls back to lower case."""
    await _client(hass, email="  User@Example.COM ").authenticate()
    usernames = [
        b["AuthParameters"]["USERNAME"]
        for t, b in fake_cloud.cognito.requests
        if t == "InitiateAuth"
    ]
    assert usernames == ["User@Example.COM", "user@example.com"]


async def test_email_case_insensitive_pool(
    hass: HomeAssistant, fake_cloud: FakeCloud
) -> None:
    """A pool that ignores case accepts the email as entered."""
    fake_cloud.cognito.case_sensitive_usernames = False
    await _client(hass, email="User@Example.com").authenticate()
    assert fake_cloud.cognito.count("InitiateAuth") == 1


async def test_email_case_fallback_wrong_password(
    hass: HomeAssistant, fake_cloud: FakeCloud
) -> None:
    """The lower-case retry reports the password error, not 'user not found'."""
    with pytest.raises(MopekaAuthError) as err:
        await _client(hass, email="User@example.com", password="bad").authenticate()
    assert err.value.code == "NotAuthorizedException"


@pytest.mark.parametrize("challenge", ["SOFTWARE_TOKEN_MFA", "NEW_PASSWORD_REQUIRED"])
async def test_unsupported_challenge(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, challenge: str
) -> None:
    """Sign-in steps that need a person are reported as auth errors."""
    fake_cloud.cognito.extra_challenge = challenge
    with pytest.raises(MopekaAuthError, match=challenge) as err:
        await api_client.authenticate()
    assert err.value.code == challenge


@pytest.mark.parametrize(
    ("error_type", "status", "expected"),
    [
        ("NotAuthorizedException", 400, MopekaAuthError),
        ("com.amazonaws.cognito#NotAuthorizedException", 400, MopekaAuthError),
        ("PasswordResetRequiredException", 400, MopekaAuthError),
        ("UserNotConfirmedException", 400, MopekaAuthError),
        ("TooManyRequestsException", 400, MopekaConnectionError),
        ("LimitExceededException", 400, MopekaConnectionError),
        ("InternalErrorException", 500, MopekaConnectionError),
        ("SomethingNew", 503, MopekaConnectionError),
        ("ResourceNotFoundException", 400, MopekaApiError),
        ("InvalidParameterException", 400, MopekaApiError),
    ],
)
async def test_cognito_error_mapping(
    api_client: MopekaApiClient,
    fake_cloud: FakeCloud,
    error_type: str,
    status: int,
    expected: type[MopekaError],
) -> None:
    """Each Cognito error type maps to the right exception."""
    fake_cloud.cognito.queue_error(error_type, "details", status)
    with pytest.raises(expected):
        await api_client.authenticate()


@pytest.mark.parametrize(
    "exc", [aiohttp.ClientConnectionError("refused"), TimeoutError()]
)
async def test_cognito_unreachable(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, exc: Exception
) -> None:
    """Network failures and timeouts are connection errors."""
    fake_cloud.cognito.queue(exc)
    with pytest.raises(MopekaConnectionError):
        await api_client.authenticate()


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (200, {"ChallengeParameters": {}}),
        (200, {"ChallengeName": "PASSWORD_VERIFIER"}),
        (
            200,
            {
                "ChallengeName": "PASSWORD_VERIFIER",
                "ChallengeParameters": {"SALT": "00"},
            },
        ),
        (200, {"AuthenticationResult": {"AccessToken": "a"}}),
        (200, ["not", "an", "object"]),
        (403, None),
    ],
)
async def test_cognito_unexpected_response(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, status: int, payload: Any
) -> None:
    """Malformed Cognito responses are API errors, not crashes."""
    fake_cloud.cognito.queue(_cognito_response(status, payload))
    with pytest.raises(MopekaApiError):
        await api_client.authenticate()


async def test_cognito_non_json_error(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """A non-JSON error page from Cognito is reported with its status."""
    fake_cloud.cognito.queue(
        AiohttpClientMockResponse("post", URL(COGNITO_URL), status=502, text="<html>")
    )
    with pytest.raises(MopekaConnectionError, match="HTTP 502"):
        await api_client.authenticate()


async def test_token_without_expiry_or_refresh_token(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, freezer: FrozenDateTimeFactory
) -> None:
    """Missing ExpiresIn defaults to an hour; no refresh token means signing in again."""
    fake_cloud.cognito.queue(
        _cognito_response(
            200,
            {
                "AuthenticationResult": {
                    "AccessToken": "a",
                    "IdToken": "i",
                    "ExpiresIn": "soon",
                }
            },
        )
    )
    await api_client.authenticate()
    assert fake_cloud.cognito.count("RespondToAuthChallenge") == 0

    freezer.tick(3600)
    await api_client.authenticate()
    assert fake_cloud.cognito.count("InitiateAuth", "USER_SRP_AUTH") == 2
    assert fake_cloud.cognito.count("InitiateAuth", "REFRESH_TOKEN_AUTH") == 0


# Token lifetime


async def test_tokens_are_reused(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """Polling again does not sign in again."""
    await api_client.get_account_data()
    await api_client.get_account_data()
    assert fake_cloud.cognito.count("InitiateAuth") == 1


async def test_refresh_before_expiry(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, freezer: FrozenDateTimeFactory
) -> None:
    """Tokens close to expiry are refreshed with the refresh token."""
    await api_client.get_account_data()
    fake_cloud.cognito.expire_all_tokens()
    freezer.tick(fake_cloud.cognito.expires_in - 30)

    data = await api_client.get_account_data()

    assert len(data.tanks) == 2
    assert fake_cloud.cognito.count("InitiateAuth", "REFRESH_TOKEN_AUTH") == 1
    assert fake_cloud.cognito.count("InitiateAuth", "USER_SRP_AUTH") == 1


async def test_refresh_rejected_signs_in_again(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, freezer: FrozenDateTimeFactory
) -> None:
    """A revoked refresh token falls back to a full sign-in."""
    await api_client.get_account_data()
    fake_cloud.cognito.expire_all_tokens()
    fake_cloud.cognito.revoke_refresh_tokens()
    freezer.tick(fake_cloud.cognito.expires_in)

    await api_client.get_account_data()

    assert fake_cloud.cognito.count("InitiateAuth", "REFRESH_TOKEN_AUTH") == 1
    assert fake_cloud.cognito.count("InitiateAuth", "USER_SRP_AUTH") == 2


async def test_password_changed_after_sign_in(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """If every token is revoked and the password changed, polling raises an auth error."""
    await api_client.get_account_data()
    fake_cloud.cognito.users[EMAIL].password = "changed"
    fake_cloud.cognito.expire_all_tokens()
    fake_cloud.cognito.revoke_refresh_tokens()

    with pytest.raises(MopekaAuthError):
        await api_client.get_account_data()


async def test_gateway_rejection_triggers_refresh(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """A 401 from the gateway refreshes the token and retries once."""
    await api_client.get_account_data()
    fake_cloud.cognito.expire_all_tokens()

    data = await api_client.get_account_data()

    assert len(data.tanks) == 2
    assert fake_cloud.cognito.count("InitiateAuth", "REFRESH_TOKEN_AUTH") == 1


async def test_concurrent_rejections_share_one_refresh(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """Requests rejected at the same time trigger only one refresh."""
    await api_client.get_account_data()
    fake_cloud.cognito.expire_all_tokens()

    await asyncio.gather(
        api_client.get_latest_reading(ADDRESS_A),
        api_client.get_latest_reading(ADDRESS_B),
    )

    assert fake_cloud.cognito.count("InitiateAuth", "REFRESH_TOKEN_AUTH") == 1


async def test_gateway_wants_id_token(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """If the gateway rejects a fresh access token, the ID token is used instead."""
    fake_cloud.gateway.accepted_token = "id"

    data = await api_client.get_account_data()
    assert len(data.tanks) == 2

    fake_cloud.gateway.requests.clear()
    await api_client.get_account_data()
    # The ID token is now used straight away, with no rejected requests.
    assert len(fake_cloud.gateway.requests) == 3
    assert all(
        h["Auth"] in fake_cloud.cognito.id_tokens
        for _, h in fake_cloud.gateway.requests
    )


async def test_gateway_rejects_every_token(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """If the gateway rejects both fresh tokens, the error says so."""
    fake_cloud.gateway.accepted_token = "neither"
    with pytest.raises(MopekaApiError, match="rejected newly issued"):
        await api_client.get_devices()
    # The next attempt starts with the access token again.
    fake_cloud.gateway.requests.clear()
    with pytest.raises(MopekaApiError):
        await api_client.get_devices()
    first_token = fake_cloud.gateway.requests[0][1]["Auth"]
    assert first_token in fake_cloud.cognito.access_tokens


# Gateway responses


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_gateway_response(500, json={"message": "boom"}), MopekaConnectionError),
        (_gateway_response(503, text="<html>"), MopekaConnectionError),
        (_gateway_response(429, json={"message": "slow down"}), MopekaConnectionError),
        (_gateway_response(404, json={"message": "nope"}), MopekaApiError),
        (_gateway_response(200, text="<html>maintenance</html>"), MopekaApiError),
        (_gateway_response(200, json=["unexpected"]), MopekaApiError),
    ],
)
async def test_device_list_errors(
    api_client: MopekaApiClient,
    fake_cloud: FakeCloud,
    response: AiohttpClientMockResponse,
    expected: type[MopekaError],
) -> None:
    """Gateway failures map to the right exception."""
    fake_cloud.gateway.device_list_override = response
    with pytest.raises(expected):
        await api_client.get_devices()


@pytest.mark.parametrize(
    "exc", [aiohttp.ClientConnectionError("reset"), TimeoutError()]
)
async def test_gateway_unreachable(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, exc: Exception
) -> None:
    """Network failures and timeouts are connection errors."""
    fake_cloud.gateway.queued_exceptions.append(exc)
    with pytest.raises(MopekaConnectionError):
        await api_client.get_devices()


async def test_device_list_without_devices_key(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    """A device list without a 'devices' array is treated as empty and logged."""
    fake_cloud.gateway.device_list_override = _gateway_response(200, json={"items": []})
    assert await api_client.get_devices() == []
    assert "no 'devices' array" in caplog.text


async def test_device_entries_without_address_are_skipped(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """Device entries that are not objects or have no address are skipped."""
    fake_cloud.gateway.devices += [{"name": "No address"}, "junk"]
    devices = await api_client.get_devices()
    assert [d.address for d in devices] == [ADDRESS_A, ADDRESS_B]


async def test_excluded_devices_are_not_read(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """Excluded devices are listed but their readings are not requested."""
    data = await api_client.get_account_data(frozenset({ADDRESS_B}))
    assert set(data.devices) == {ADDRESS_A, ADDRESS_B}
    assert set(data.tanks) == {ADDRESS_A}
    assert not any(ADDRESS_B in path for path, _ in fake_cloud.gateway.requests)


async def test_device_without_reading(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """A device with no cloud reading, such as a Bluetooth-only sensor, is not a tank."""
    fake_cloud.gateway.add_tank(make_device("AA:BB:CC:00:00:03", "Bluetooth only"))
    data = await api_client.get_account_data()
    assert "AA:BB:CC:00:00:03" in data.devices
    assert "AA:BB:CC:00:00:03" not in data.tanks


@pytest.mark.parametrize(
    "payload", [{"timeSeries": {}}, {"timeSeries": {"Items": ["x"]}}, {"other": 1}, []]
)
async def test_reading_payload_shapes(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, payload: Any
) -> None:
    """Unexpected data-shadow shapes mean 'no reading', not a crash."""
    fake_cloud.gateway.readings[ADDRESS_A] = []
    await api_client.authenticate()
    fake_cloud.gateway.device_list_override = None

    async def shadow(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        return _gateway_response(200, json=payload)

    fake_cloud.gateway.mocker.clear_requests()
    fake_cloud.gateway.mocker.get(
        GATEWAY_URL + f"/{ADDRESS_A}/data-shadow", side_effect=shadow
    )
    assert await api_client.get_latest_reading(ADDRESS_A) is None


async def test_one_device_failing(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    """A device whose reading fails is left out; the others still update."""
    fake_cloud.gateway.reading_status[ADDRESS_B] = HTTPStatus.INTERNAL_SERVER_ERROR
    data = await api_client.get_account_data()
    assert set(data.tanks) == {ADDRESS_A}
    assert "Cannot read Tank B" in caplog.text


async def test_every_device_failing(
    api_client: MopekaApiClient, fake_cloud: FakeCloud
) -> None:
    """If no device can be read, the poll fails."""
    fake_cloud.gateway.reading_status[ADDRESS_A] = HTTPStatus.BAD_GATEWAY
    fake_cloud.gateway.reading_status[ADDRESS_B] = HTTPStatus.BAD_GATEWAY
    with pytest.raises(MopekaConnectionError):
        await api_client.get_account_data()


async def test_auth_error_while_reading_propagates(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auth error for one device fails the whole poll so re-auth can start."""

    async def fail(address: str) -> None:
        raise MopekaAuthError("rejected", code="NotAuthorizedException")

    monkeypatch.setattr(api_client, "get_latest_reading", fail)
    with pytest.raises(MopekaAuthError):
        await api_client.get_account_data()


async def test_unexpected_exception_while_reading_propagates(
    api_client: MopekaApiClient, fake_cloud: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Programming errors are not swallowed."""

    async def fail(address: str) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(api_client, "get_latest_reading", fail)
    with pytest.raises(RuntimeError):
        await api_client.get_account_data()


# Logging


async def test_debug_log_is_redacted(
    hass: HomeAssistant, fake_cloud: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    """Debug logs show each call and its status but no secrets or email."""
    caplog.set_level(logging.DEBUG, logger="custom_components.mopeka_ha")
    fake_cloud.gateway.accepted_token = "id"
    await _client(hass).get_account_data()

    log = caplog.text
    assert "Cognito InitiateAuth request" in log
    assert "Cognito RespondToAuthChallenge response: HTTP 200" in log
    assert "Gateway GET" in log and "HTTP 401" in log and "HTTP 200" in log
    assert "trying the id token" in log
    assert REDACTED in log

    assert PASSWORD not in log
    assert EMAIL not in log
    user = fake_cloud.cognito.users[EMAIL]
    assert user.user_id not in log
    assert user.salt not in log
    for token in (
        fake_cloud.cognito.access_tokens
        | fake_cloud.cognito.id_tokens
        | fake_cloud.cognito.refresh_tokens
    ):
        assert token not in log
    assert "eyJ" not in log


def test_redact() -> None:
    """Secrets are removed by key, and emails and JWTs by pattern."""
    data = {
        "AuthenticationResult": {"AccessToken": "secret", "ExpiresIn": 3600},
        "list": [{"Session": "s"}, "contact me@example.org now"],
        "text": "token eyJabc.def.ghi end",
        "number": 5,
    }
    assert redact(data) == {
        "AuthenticationResult": {"AccessToken": REDACTED, "ExpiresIn": 3600},
        "list": [{"Session": REDACTED}, f"contact {REDACTED} now"],
        "text": f"token {REDACTED} end",
        "number": 5,
    }


def test_loggable_body() -> None:
    """Bodies are redacted whether or not they are JSON, and long ones truncated."""
    assert _loggable_body('{"IdToken": "x"}') == f'{{"IdToken": "{REDACTED}"}}'
    assert _loggable_body("plain a@b.co") == f"plain {REDACTED}"
    long_body = _loggable_body("x" * 5000)
    assert long_body.endswith("... (5000 characters)")
    assert len(long_body) < 4100
