"""Client for the Mopeka Cloud APIs used by Mopeka's mobile apps.

Mopeka runs two platforms, each with its own Amazon Cognito user pool:

- the current platform (mopeka.cloud), used by the Co-Pilot app. Retail
  accounts read data with one sync request to /api/devices/retail;
- the older platform used by the Tank Check app, with its own gateway.

Sign-in tries the current platform first and falls back to the older one, then
remembers which platform holds the account. Both use Cognito's Secure Remote
Password (SRP) flow over plain HTTPS, as the apps' AWS Amplify library does.
The SRP math comes from pycognito, which Home Assistant already ships;
pycognito's boto3 client is not used.

Every request is logged at debug level with tokens, SRP values, and email
addresses removed, so a debug log shows exactly what failed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Final, Literal

import aiohttp
from pycognito.aws_srp import AWSSRP, hex_to_long
from yarl import URL

from .const import (
    BACKEND_LEGACY,
    BACKEND_RETAIL,
    CURRENT_POOL,
    EMPTY_RETAIL_SYNC,
    GATEWAY_HEADERS,
    GATEWAY_URL,
    LEGACY_POOL,
    REQUEST_TIMEOUT,
    RETAIL_HEADERS,
    RETAIL_SYNC_URL,
    CognitoPool,
)
from .models import MopekaDevice, MopekaReading, MopekaTank

_LOGGER = logging.getLogger(__name__)

REDACTED: Final = "**REDACTED**"

# Keys whose values are secrets or personal data, wherever they appear.
_REDACT_KEYS: Final = frozenset(
    {
        "AccessToken",
        "Auth",
        "Authorization",
        "DeviceGroupKey",
        "DeviceKey",
        "IdToken",
        "PASSWORD",
        "PASSWORD_CLAIM_SECRET_BLOCK",
        "PASSWORD_CLAIM_SIGNATURE",
        "REFRESH_TOKEN",
        "RefreshToken",
        "SALT",
        "SECRET_BLOCK",
        "SECRET_HASH",
        "SRP_A",
        "SRP_B",
        "Session",
        "USERNAME",
        "USER_ID_FOR_SRP",
    }
)
_EMAIL_RE: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_JWT_RE: Final = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
_MAX_LOGGED_BODY: Final = 4000

# Cognito error types that mean the credentials were not accepted.
_COGNITO_AUTH_ERRORS: Final = frozenset(
    {
        "NotAuthorizedException",
        "PasswordResetRequiredException",
        "UserNotConfirmedException",
        "UserNotFoundException",
    }
)
# Cognito error types that are temporary and worth retrying later.
_COGNITO_TRANSIENT_ERRORS: Final = frozenset(
    {
        "InternalErrorException",
        "LimitExceededException",
        "ServiceUnavailableException",
        "TooManyRequestsException",
    }
)

# Refresh tokens this long before they expire.
_TOKEN_EXPIRY_MARGIN: Final = 60  # seconds

TokenKind = Literal["access", "id"]

_POOLS: Final = {BACKEND_RETAIL: CURRENT_POOL, BACKEND_LEGACY: LEGACY_POOL}
# Errors meaning "this pool does not know these credentials". The current pool
# hides whether an account exists, so it answers NotAuthorizedException even for
# accounts that only exist in the older pool.
_ACCOUNT_NOT_IN_POOL: Final = frozenset(
    {"NotAuthorizedException", "UserNotFoundException"}
)


class MopekaError(Exception):
    """Base class for Mopeka Cloud errors."""


class MopekaConnectionError(MopekaError):
    """The service could not be reached, timed out, or is temporarily failing."""


class MopekaAuthError(MopekaError):
    """The service did not accept the email and password."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Initialize with a message and the Cognito error type or challenge."""
        super().__init__(message)
        self.code = code


class MopekaApiError(MopekaError):
    """The service returned a response this client does not understand."""


def redact(data: Any) -> Any:
    """Return a copy of data with secrets and email addresses removed."""
    if isinstance(data, dict):
        return {
            key: REDACTED if key in _REDACT_KEYS else redact(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(value) for value in data]
    if isinstance(data, str):
        return _JWT_RE.sub(REDACTED, _EMAIL_RE.sub(REDACTED, data))
    return data


def _loggable_body(text: str) -> str:
    """Return a redacted, length-limited response body for the debug log."""
    try:
        body = json.dumps(redact(json.loads(text)))
    except ValueError:
        body = redact(text)
    if len(body) > _MAX_LOGGED_BODY:
        return f"{body[:_MAX_LOGGED_BODY]}... ({len(body)} characters)"
    return body


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _password_verifier_response(
    srp: AWSSRP, params: dict[str, Any], username: str
) -> dict[str, str]:
    """Answer a PASSWORD_VERIFIER challenge with the SRP password proof.

    Equivalent to AWSSRP.process_challenge, which relies on the deprecated
    datetime.utcnow().
    """
    user_id = str(params["USER_ID_FOR_SRP"])
    secret_block = str(params["SECRET_BLOCK"])
    key: bytes = srp.get_password_authentication_key(
        user_id, srp.password, hex_to_long(params["SRP_B"]), params["SALT"]
    )
    timestamp: str = srp.get_cognito_formatted_timestamp(datetime.now(UTC))
    message = (
        str(srp.pool_id).split("_")[1].encode()
        + user_id.encode()
        + base64.standard_b64decode(secret_block)
        + timestamp.encode()
    )
    signature = hmac.new(key, message, hashlib.sha256).digest()
    return {
        "TIMESTAMP": timestamp,
        "USERNAME": str(params.get("USERNAME", username)),
        "PASSWORD_CLAIM_SECRET_BLOCK": secret_block,
        "PASSWORD_CLAIM_SIGNATURE": base64.standard_b64encode(signature).decode(),
    }


def _sample_time(sample: dict[str, Any]) -> float:
    """Return a retail sample's timestamp for sorting, or 0 if it has none."""
    value = sample.get("t")
    return float(value) if isinstance(value, int | float) else 0.0


class _NoBotoClient:
    """Stands in for the boto3 client AWSSRP would otherwise create.

    Only AWSSRP's SRP math is used; all requests go through aiohttp.
    """


@dataclass(slots=True)
class _Tokens:
    access_token: str
    id_token: str
    refresh_token: str | None
    expires_at: float
    pool: CognitoPool

    def expired(self) -> bool:
        return time.time() >= self.expires_at - _TOKEN_EXPIRY_MARGIN

    def get(self, kind: TokenKind) -> str:
        return self.access_token if kind == "access" else self.id_token


@dataclass(frozen=True, slots=True)
class MopekaAccountData:
    """Everything one poll of the account returns."""

    devices: dict[str, MopekaDevice] = field(default_factory=dict)
    tanks: dict[str, MopekaTank] = field(default_factory=dict)


class MopekaApiClient:
    """Read tank sensors from Mopeka Cloud."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        backend: str | None = None,
    ) -> None:
        """Initialize the client. No request is made until data is requested.

        ``backend`` is BACKEND_RETAIL or BACKEND_LEGACY if known, or None to
        detect it at the first sign-in.
        """
        self._session = session
        self._email = email
        self._password = password
        self._backend = backend if backend in _POOLS else None
        self._tokens: _Tokens | None = None
        self._token_lock = asyncio.Lock()
        # The app's token type for the gateway's Auth header. The access token
        # is known to work; the ID token is tried if the gateway rejects it.
        self._token_kind: TokenKind = "access"  # noqa: S105 - a token type
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    @property
    def backend(self) -> str | None:
        """Return the platform holding the account, once known."""
        return self._backend

    # Authentication

    async def authenticate(self) -> None:
        """Sign in, or refresh the current tokens if they are about to expire."""
        await self._ensure_tokens()

    async def _ensure_tokens(self, rejected: _Tokens | None = None) -> _Tokens:
        """Return valid tokens, signing in or refreshing as needed.

        Pass the tokens the gateway rejected as ``rejected`` to force a refresh.
        Concurrent callers that saw the same rejection share one refresh.
        """
        async with self._token_lock:
            tokens = self._tokens
            if (
                tokens is not None
                and not tokens.expired()
                and (rejected is None or tokens is not rejected)
            ):
                return tokens
            if tokens is not None and tokens.refresh_token:
                try:
                    self._tokens = await self._refresh(
                        tokens.refresh_token, tokens.pool
                    )
                except MopekaAuthError as err:
                    _LOGGER.debug("Refresh token not accepted (%s); signing in", err)
                else:
                    return self._tokens
            self._tokens = await self._login()
            return self._tokens

    def _username_candidates(self) -> list[str]:
        """Return the usernames to try, in order.

        Cognito usernames can be case sensitive. If the email as entered is not
        found, the lower-case form is tried, since that is how most accounts
        are registered.
        """
        entered = self._email.strip()
        lowered = entered.lower()
        return [entered] if lowered == entered else [entered, lowered]

    async def _login(self) -> _Tokens:
        """Sign in to the account's platform, detecting it if not yet known."""
        if self._backend is not None:
            return await self._login_to_pool(_POOLS[self._backend])
        try:
            tokens = await self._login_to_pool(CURRENT_POOL)
        except MopekaAuthError as current_error:
            if current_error.code not in _ACCOUNT_NOT_IN_POOL:
                raise
            _LOGGER.debug(
                "Current platform did not accept the sign-in (%s); trying the "
                "older platform",
                current_error,
            )
            try:
                tokens = await self._login_to_pool(LEGACY_POOL)
            except MopekaAuthError as legacy_error:
                if legacy_error.code not in _ACCOUNT_NOT_IN_POOL:
                    raise  # The older platform knows the account; report why.
                _LOGGER.debug("Older platform did not either: %s", legacy_error)
                raise current_error from None
            self._backend = BACKEND_LEGACY
        else:
            self._backend = BACKEND_RETAIL
        _LOGGER.debug("Account is on the %s platform", self._backend)
        return tokens

    async def _login_to_pool(self, pool: CognitoPool) -> _Tokens:
        candidates = self._username_candidates()
        for index, username in enumerate(candidates):
            try:
                return await self._srp_login(username, pool)
            except MopekaAuthError as err:
                if err.code != "UserNotFoundException" or index == len(candidates) - 1:
                    raise
                _LOGGER.debug("Account not found as entered; trying lower-case email")
        raise AssertionError("unreachable")  # pragma: no cover

    async def _srp_login(self, username: str, pool: CognitoPool) -> _Tokens:
        loop = asyncio.get_running_loop()
        srp: AWSSRP = await loop.run_in_executor(
            None,
            lambda: AWSSRP(
                username=username,
                password=self._password,
                pool_id=pool.pool_id,
                client_id=pool.client_id,
                client=_NoBotoClient(),
            ),
        )
        auth_params: dict[str, str] = srp.get_auth_params()
        response = await self._cognito(
            pool,
            "InitiateAuth",
            {
                "AuthFlow": "USER_SRP_AUTH",
                "ClientId": pool.client_id,
                "AuthParameters": auth_params,
                "ClientMetadata": {},
            },
        )
        challenge = response.get("ChallengeName")
        if challenge != "PASSWORD_VERIFIER":
            return self._tokens_from(response, pool, previous_refresh_token=None)

        challenge_params = response.get("ChallengeParameters")
        if not isinstance(challenge_params, dict):
            raise MopekaApiError("Cognito sent PASSWORD_VERIFIER without parameters")
        try:
            challenge_responses = await loop.run_in_executor(
                None, _password_verifier_response, srp, challenge_params, username
            )
        except (KeyError, ValueError) as err:
            raise MopekaApiError(f"Cannot answer Cognito SRP challenge: {err}") from err

        request: dict[str, Any] = {
            "ChallengeName": "PASSWORD_VERIFIER",
            "ClientId": pool.client_id,
            "ChallengeResponses": challenge_responses,
            "ClientMetadata": {},
        }
        if session := response.get("Session"):
            request["Session"] = session
        response = await self._cognito(pool, "RespondToAuthChallenge", request)
        return self._tokens_from(response, pool, previous_refresh_token=None)

    async def _refresh(self, refresh_token: str, pool: CognitoPool) -> _Tokens:
        response = await self._cognito(
            pool,
            "InitiateAuth",
            {
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "ClientId": pool.client_id,
                "AuthParameters": {"REFRESH_TOKEN": refresh_token},
            },
        )
        return self._tokens_from(response, pool, previous_refresh_token=refresh_token)

    @staticmethod
    def _tokens_from(
        response: dict[str, Any],
        pool: CognitoPool,
        previous_refresh_token: str | None,
    ) -> _Tokens:
        """Extract tokens from a Cognito response, or explain why there are none."""
        if challenge := response.get("ChallengeName"):
            # MFA, a forced password change, and similar steps need a person.
            raise MopekaAuthError(
                f"Sign-in requires the {challenge} step, which this integration "
                "does not support. Complete it in the Mopeka app, then retry.",
                code=str(challenge),
            )
        result = response.get("AuthenticationResult")
        if not isinstance(result, dict):
            raise MopekaApiError("Cognito response has no AuthenticationResult")
        access_token = result.get("AccessToken")
        id_token = result.get("IdToken")
        if not isinstance(access_token, str) or not isinstance(id_token, str):
            raise MopekaApiError("Cognito response is missing AccessToken or IdToken")
        try:
            expires_in = int(result.get("ExpiresIn", 3600))
        except TypeError, ValueError:
            expires_in = 3600
        refresh_token = result.get("RefreshToken")
        return _Tokens(
            access_token=access_token,
            id_token=id_token,
            refresh_token=(
                refresh_token
                if isinstance(refresh_token, str)
                else previous_refresh_token
            ),
            expires_at=time.time() + expires_in,
            pool=pool,
        )

    async def _cognito(
        self, pool: CognitoPool, target: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Call one Cognito Identity Provider operation and return its JSON."""
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{target}",
        }
        _LOGGER.debug("Cognito %s request: %s", target, redact(body))
        started = time.monotonic()
        try:
            async with self._session.post(
                pool.url,
                data=json.dumps(body),
                headers=headers,
                timeout=self._timeout,
            ) as resp:
                status = resp.status
                text = await resp.text()
        except TimeoutError as err:
            _LOGGER.debug("Cognito %s timed out after %ss", target, REQUEST_TIMEOUT)
            raise MopekaConnectionError(
                f"Timed out contacting Amazon Cognito ({target})"
            ) from err
        except aiohttp.ClientError as err:
            _LOGGER.debug("Cognito %s failed: %r", target, err)
            raise MopekaConnectionError(
                f"Cannot reach Amazon Cognito ({target}): {err}"
            ) from err

        _LOGGER.debug(
            "Cognito %s response: HTTP %s in %d ms: %s",
            target,
            status,
            (time.monotonic() - started) * 1000,
            _loggable_body(text),
        )
        payload = _parse_json(text)
        if status == HTTPStatus.OK and isinstance(payload, dict):
            return payload

        error_type = ""
        message = ""
        if isinstance(payload, dict):
            # __type can be qualified, e.g. "com.amazon...#NotAuthorizedException".
            error_type = str(payload.get("__type", "")).rsplit("#", 1)[-1]
            message = str(payload.get("message") or payload.get("Message") or "")
        detail = f"{error_type or f'HTTP {status}'}: {message}".rstrip(": ")
        if error_type in _COGNITO_AUTH_ERRORS:
            raise MopekaAuthError(f"Sign-in rejected: {detail}", code=error_type)
        if (
            error_type in _COGNITO_TRANSIENT_ERRORS
            or status >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            raise MopekaConnectionError(f"Amazon Cognito is unavailable: {detail}")
        raise MopekaApiError(f"Amazon Cognito {target} failed: {detail}")

    # Data requests

    async def _send(
        self,
        label: str,
        method: str,
        url: URL,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        body: str | None = None,
    ) -> tuple[int, Any]:
        """Send one data request and return its status and parsed JSON body."""
        _LOGGER.debug("%s %s %s params=%s", label, method, url, params)
        started = time.monotonic()
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                headers=headers,
                data=body,
                timeout=self._timeout,
            ) as resp:
                status = resp.status
                text = await resp.text()
        except TimeoutError as err:
            _LOGGER.debug(
                "%s %s %s timed out after %ss", label, method, url, REQUEST_TIMEOUT
            )
            raise MopekaConnectionError("Timed out contacting Mopeka Cloud") from err
        except aiohttp.ClientError as err:
            _LOGGER.debug("%s %s %s failed: %r", label, method, url, err)
            raise MopekaConnectionError(f"Cannot reach Mopeka Cloud: {err}") from err

        _LOGGER.debug(
            "%s %s %s response: HTTP %s in %d ms: %s",
            label,
            method,
            url,
            status,
            (time.monotonic() - started) * 1000,
            _loggable_body(text),
        )
        payload = _parse_json(text)
        if status == HTTPStatus.OK and payload is None:
            raise MopekaApiError(f"Mopeka Cloud returned non-JSON data from {url}")
        return status, payload

    # Current platform: retail sync

    async def _retail_request(self, tokens: _Tokens) -> tuple[int, Any]:
        return await self._send(
            "Retail sync",
            "PUT",
            URL(RETAIL_SYNC_URL),
            params={"lastUpdatedAt": "0"},
            headers={
                **RETAIL_HEADERS,
                "Content-Type": "application/json",
                "token": tokens.access_token,
            },
            body=json.dumps(EMPTY_RETAIL_SYNC),
        )

    async def _retail_sync(self) -> Any:
        """Return the retail sync response, refreshing tokens once on HTTP 401."""
        tokens = await self._ensure_tokens()
        status, payload = await self._retail_request(tokens)
        if status == HTTPStatus.UNAUTHORIZED:
            _LOGGER.debug("Retail sync rejected the token; refreshing")
            tokens = await self._ensure_tokens(rejected=tokens)
            status, payload = await self._retail_request(tokens)
            if status == HTTPStatus.UNAUTHORIZED:
                raise MopekaApiError("Mopeka Cloud rejected a newly issued token")
        if status == HTTPStatus.FORBIDDEN:
            raise MopekaApiError(
                "Mopeka Cloud refused to share this account's devices (HTTP 403)"
            )
        if (
            status == HTTPStatus.TOO_MANY_REQUESTS
            or status >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            raise MopekaConnectionError(f"Mopeka Cloud is unavailable (HTTP {status})")
        if status != HTTPStatus.OK:
            raise MopekaApiError(f"Mopeka Cloud returned HTTP {status} for the sync")
        return payload

    async def _get_retail_account_data(
        self, excluded: frozenset[str]
    ) -> MopekaAccountData:
        payload = await self._retail_sync()
        raw_devices = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(raw_devices, list):
            raise MopekaApiError("Retail sync response has no 'devices' array")
        devices: dict[str, MopekaDevice] = {}
        tanks: dict[str, MopekaTank] = {}
        for raw in raw_devices:
            device = MopekaDevice.from_retail(raw) if isinstance(raw, dict) else None
            if device is None:
                _LOGGER.debug("Skipping device entry without a MAC: %s", redact(raw))
                continue
            devices[device.address] = device
            samples = [s for s in raw.get("samples") or [] if isinstance(s, dict)]
            if not samples:
                _LOGGER.debug("No reading in Mopeka Cloud for %s", device.address)
                continue
            if device.address in excluded:
                continue
            latest = max(samples, key=_sample_time)
            tanks[device.address] = MopekaTank(
                device=device, reading=MopekaReading.from_retail(latest)
            )
        return MopekaAccountData(devices=devices, tanks=tanks)

    # Older platform: gateway

    async def _gateway_request(
        self, url: URL, params: dict[str, str], tokens: _Tokens
    ) -> tuple[int, Any]:
        _LOGGER.debug("Gateway request uses the %s token", self._token_kind)
        return await self._send(
            "Gateway",
            "GET",
            url,
            params=params,
            headers={**GATEWAY_HEADERS, "Auth": tokens.get(self._token_kind)},
        )

    async def _gateway_get(self, url: URL, params: dict[str, str]) -> Any:
        """GET a gateway URL, refreshing tokens once if the gateway rejects them."""
        tokens = await self._ensure_tokens()
        status, payload = await self._gateway_request(url, params, tokens)

        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            _LOGGER.debug("Gateway rejected the token (HTTP %s); refreshing", status)
            tokens = await self._ensure_tokens(rejected=tokens)
            status, payload = await self._gateway_request(url, params, tokens)

        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            # The token is fresh, so the credentials are fine; the gateway may
            # expect the other token type.
            previous = self._token_kind
            self._token_kind = "id" if previous == "access" else "access"
            _LOGGER.debug(
                "Gateway rejected a fresh %s token; trying the %s token",
                previous,
                self._token_kind,
            )
            status, payload = await self._gateway_request(url, params, tokens)
            if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                self._token_kind = previous
                raise MopekaApiError(
                    "Mopeka Cloud rejected newly issued access and ID tokens "
                    f"(HTTP {status})"
                )

        if (
            status == HTTPStatus.TOO_MANY_REQUESTS
            or status >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            raise MopekaConnectionError(f"Mopeka Cloud is unavailable (HTTP {status})")
        if status != HTTPStatus.OK:
            raise MopekaApiError(f"Mopeka Cloud returned HTTP {status} for {url}")
        return payload

    async def get_devices(self) -> list[MopekaDevice]:
        """Return the sensors registered to the account."""
        payload = await self._gateway_get(
            URL(GATEWAY_URL), {"_": str(int(time.time() * 1000))}
        )
        if not isinstance(payload, dict):
            raise MopekaApiError("Device list from Mopeka Cloud is not an object")
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list):
            _LOGGER.warning(
                "Mopeka Cloud device list has no 'devices' array; keys: %s",
                sorted(payload),
            )
            return []
        devices: list[MopekaDevice] = []
        for raw in raw_devices:
            device = MopekaDevice.from_api(raw) if isinstance(raw, dict) else None
            if device is None:
                _LOGGER.debug(
                    "Skipping device entry without an address: %s", redact(raw)
                )
                continue
            devices.append(device)
        return devices

    async def get_latest_reading(self, address: str) -> MopekaReading | None:
        """Return the most recent reading for one sensor, or None if there is none."""
        payload = await self._gateway_get(
            URL(GATEWAY_URL) / address / "data-shadow",
            {"limit": "1", "shadowLimit": "1"},
        )
        series = payload.get("timeSeries") if isinstance(payload, dict) else None
        items = series.get("Items") if isinstance(series, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            _LOGGER.debug("No reading in Mopeka Cloud for %s", address)
            return None
        return MopekaReading.from_api(items[0])

    async def get_account_data(
        self, excluded: frozenset[str] = frozenset()
    ) -> MopekaAccountData:
        """Return every device on the account and the latest reading for each.

        Devices in ``excluded`` are listed but left out of ``tanks``. Devices
        without a cloud reading are also left out of ``tanks``.
        """
        await self._ensure_tokens()
        if self._backend == BACKEND_RETAIL:
            return await self._get_retail_account_data(excluded)
        return await self._get_legacy_account_data(excluded)

    async def _get_legacy_account_data(
        self, excluded: frozenset[str]
    ) -> MopekaAccountData:
        """Read the older platform, one request per device.

        A device whose reading cannot be fetched is left out of ``tanks`` for
        this poll; the poll fails only if no device could be read at all.
        """
        devices = await self.get_devices()
        to_read = [d for d in devices if d.address not in excluded]
        results = await asyncio.gather(
            *(self.get_latest_reading(d.address) for d in to_read),
            return_exceptions=True,
        )

        tanks: dict[str, MopekaTank] = {}
        errors: list[MopekaError] = []
        for device, result in zip(to_read, results, strict=True):
            if isinstance(result, MopekaAuthError):
                raise result
            if isinstance(result, MopekaError):
                _LOGGER.warning("Cannot read %s: %s", device.display_name, result)
                errors.append(result)
            elif isinstance(result, BaseException):
                raise result
            elif result is not None:
                tanks[device.address] = MopekaTank(device=device, reading=result)
        if errors and len(errors) == len(to_read):
            raise errors[0]
        return MopekaAccountData(devices={d.address: d for d in devices}, tanks=tanks)
