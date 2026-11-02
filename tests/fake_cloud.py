"""In-memory fakes of Amazon Cognito and the Mopeka gateway.

FakeCognito implements the server side of Cognito's SRP sign-in, so a client
only gets tokens if it computes a correct password proof. FakeGateway accepts
only tokens FakeCognito issued and has not expired or revoked.

Both plug into Home Assistant's ``aioclient_mock``; no network is used.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from pycognito.aws_srp import (
    G_HEX,
    N_HEX,
    calculate_u,
    compute_hkdf,
    hash_sha256,
    hex_hash,
    hex_to_long,
    long_to_hex,
    pad_hex,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.mopeka_ha.const import (
    COGNITO_CLIENT_ID,
    COGNITO_URL,
    COGNITO_USER_POOL_ID,
    GATEWAY_URL,
)

_BIG_N = hex_to_long(N_HEX)
_G = hex_to_long(G_HEX)
_K = hex_to_long(hex_hash("00" + N_HEX + "0" + G_HEX))
_POOL_NAME = COGNITO_USER_POOL_ID.split("_")[1]
_TIMESTAMP_RE = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"\d{1,2} \d{2}:\d{2}:\d{2} UTC \d{4}$"
)


def _response(status: int, payload: Any) -> AiohttpClientMockResponse:
    return AiohttpClientMockResponse(
        "post",
        URL(COGNITO_URL),
        status=status,
        json=payload,
        headers={"Content-Type": "application/x-amz-json-1.1"},
    )


def _error(
    error_type: str, message: str, status: int = 400
) -> AiohttpClientMockResponse:
    return _response(status, {"__type": error_type, "message": message})


@dataclass
class FakeUser:
    """A Cognito user with an SRP verifier, as Cognito stores it."""

    email: str
    password: str
    user_id: str = field(default_factory=lambda: f"uid-{secrets.token_hex(8)}")
    salt: str = field(default_factory=lambda: secrets.token_hex(16))

    @property
    def verifier(self) -> int:
        """Return v = g^x mod N for this user's password."""
        inner = hash_sha256(f"{_POOL_NAME}{self.user_id}:{self.password}".encode())
        x = hex_to_long(hex_hash(pad_hex(self.salt) + inner))
        return pow(_G, x, _BIG_N)


@dataclass
class _PendingChallenge:
    user: FakeUser
    srp_a: int
    small_b: int
    srp_b: int
    secret_block: str


class FakeCognito:
    """Fake Cognito Identity Provider for one user pool."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        """Initialize with no users."""
        self.mocker = mocker
        self.users: dict[str, FakeUser] = {}
        self.case_sensitive_usernames = True
        self.expires_in = 3600
        # Challenge to send after a correct password, such as "SOFTWARE_TOKEN_MFA".
        self.extra_challenge: str | None = None
        # Responses returned instead of normal handling, oldest first.
        self.queued: list[AiohttpClientMockResponse | Exception] = []
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.access_tokens: set[str] = set()
        self.id_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self._sessions: dict[str, _PendingChallenge] = {}
        self._counter = 0

    def add_user(self, email: str, password: str) -> FakeUser:
        """Register a user."""
        user = FakeUser(email=email, password=password)
        self.users[email] = user
        return user

    def queue_error(
        self, error_type: str, message: str = "", status: int = 400
    ) -> None:
        """Make the next request fail with a Cognito error."""
        self.queued.append(_error(error_type, message, status))

    def queue(self, item: AiohttpClientMockResponse | Exception) -> None:
        """Make the next request return a raw response or raise an exception."""
        self.queued.append(item)

    def expire_all_tokens(self) -> None:
        """Invalidate every access and ID token, as if they had expired."""
        self.access_tokens.clear()
        self.id_tokens.clear()

    def revoke_refresh_tokens(self) -> None:
        """Invalidate every refresh token."""
        self.refresh_tokens.clear()

    def count(self, operation: str, flow: str | None = None) -> int:
        """Return how many requests of an operation (and auth flow) were made."""
        return sum(
            1
            for target, body in self.requests
            if target == operation and (flow is None or body.get("AuthFlow") == flow)
        )

    def _find_user(self, username: str) -> FakeUser | None:
        if self.case_sensitive_usernames:
            return self.users.get(username)
        return next(
            (u for e, u in self.users.items() if e.lower() == username.lower()), None
        )

    def _new_token(self, kind: str) -> str:
        self._counter += 1
        payload = base64.urlsafe_b64encode(f'{{"k":"{kind}{self._counter}"}}'.encode())
        return f"eyJhbGciOiJSUzI1NiJ9.{payload.decode().rstrip('=')}.c2ln"

    def _authentication_result(self, include_refresh: bool) -> dict[str, Any]:
        access, id_token = self._new_token("access"), self._new_token("id")
        self.access_tokens.add(access)
        self.id_tokens.add(id_token)
        result: dict[str, Any] = {
            "AccessToken": access,
            "IdToken": id_token,
            "ExpiresIn": self.expires_in,
            "TokenType": "Bearer",
        }
        if include_refresh:
            refresh = self._new_token("refresh")
            self.refresh_tokens.add(refresh)
            result["RefreshToken"] = refresh
        return {"AuthenticationResult": result, "ChallengeParameters": {}}

    async def handle(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        """Handle one request routed here by aioclient_mock."""
        headers = self.mocker.mock_calls[-1][3] or {}
        target = str(headers.get("X-Amz-Target", "")).rsplit(".", 1)[-1]
        body = json.loads(data)
        self.requests.append((target, body))
        assert headers.get("Content-Type") == "application/x-amz-json-1.1"
        assert body.get("ClientId") == COGNITO_CLIENT_ID

        if self.queued:
            item = self.queued.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if target == "InitiateAuth":
            return self._initiate_auth(body)
        if target == "RespondToAuthChallenge":
            return self._respond_to_auth_challenge(body)
        return _error("InvalidActionException", f"Unknown target {target}")

    def _initiate_auth(self, body: dict[str, Any]) -> AiohttpClientMockResponse:
        params = body.get("AuthParameters", {})
        flow = body.get("AuthFlow")

        if flow == "REFRESH_TOKEN_AUTH":
            if params.get("REFRESH_TOKEN") not in self.refresh_tokens:
                return _error("NotAuthorizedException", "Invalid Refresh Token")
            return _response(HTTPStatus.OK, self._authentication_result(False))

        if flow != "USER_SRP_AUTH":
            return _error("InvalidParameterException", f"Unsupported flow {flow}")
        user = self._find_user(params.get("USERNAME", ""))
        if user is None:
            return _error("UserNotFoundException", "User does not exist.")

        srp_a = hex_to_long(params["SRP_A"])
        if srp_a % _BIG_N == 0:
            return _error("InvalidParameterException", "Invalid SRP_A")
        small_b = hex_to_long(secrets.token_hex(128)) % _BIG_N
        srp_b = (_K * user.verifier + pow(_G, small_b, _BIG_N)) % _BIG_N
        session = secrets.token_hex(16)
        secret_block = base64.standard_b64encode(secrets.token_bytes(64)).decode()
        self._sessions[session] = _PendingChallenge(
            user, srp_a, small_b, srp_b, secret_block
        )
        return _response(
            HTTPStatus.OK,
            {
                "ChallengeName": "PASSWORD_VERIFIER",
                "Session": session,
                "ChallengeParameters": {
                    "SALT": user.salt,
                    "SECRET_BLOCK": secret_block,
                    "SRP_B": long_to_hex(srp_b),
                    "USERNAME": user.user_id,
                    "USER_ID_FOR_SRP": user.user_id,
                },
            },
        )

    def _respond_to_auth_challenge(
        self, body: dict[str, Any]
    ) -> AiohttpClientMockResponse:
        if body.get("ChallengeName") != "PASSWORD_VERIFIER":
            return _error("InvalidParameterException", "Unexpected challenge")
        pending = self._sessions.pop(body.get("Session", ""), None)
        if pending is None:
            return _error("NotAuthorizedException", "Invalid session for the user.")
        responses = body.get("ChallengeResponses", {})
        timestamp = responses.get("TIMESTAMP", "")
        if not _TIMESTAMP_RE.match(timestamp):
            return _error("InvalidParameterException", "Invalid TIMESTAMP")
        if responses.get("USERNAME") != pending.user.user_id:
            return _error("NotAuthorizedException", "Incorrect username or password.")

        # Server side of SRP: S = (A * v^u)^b mod N.
        u = calculate_u(pending.srp_a, pending.srp_b)
        shared = pow(
            pending.srp_a * pow(pending.user.verifier, u, _BIG_N),
            pending.small_b,
            _BIG_N,
        )
        hkdf = compute_hkdf(
            bytearray.fromhex(pad_hex(shared)),
            bytearray.fromhex(pad_hex(long_to_hex(u))),
        )
        message = (
            _POOL_NAME.encode()
            + pending.user.user_id.encode()
            + base64.standard_b64decode(pending.secret_block)
            + timestamp.encode()
        )
        expected = base64.standard_b64encode(
            hmac.new(hkdf, message, hashlib.sha256).digest()
        ).decode()
        if not hmac.compare_digest(
            expected, responses.get("PASSWORD_CLAIM_SIGNATURE", "")
        ):
            return _error("NotAuthorizedException", "Incorrect username or password.")

        if self.extra_challenge:
            return _response(
                HTTPStatus.OK,
                {
                    "ChallengeName": self.extra_challenge,
                    "Session": secrets.token_hex(16),
                    "ChallengeParameters": {},
                },
            )
        return _response(HTTPStatus.OK, self._authentication_result(True))


def make_device(
    address: str,
    name: str,
    *,
    tank_type: str = "20lb",
    tank_height: float | None = 0.3,
    vertical: bool = True,
    model: str = "M1017",
) -> dict[str, Any]:
    """Return a device entry shaped like the gateway's device list."""
    return {
        "address": address,
        "name": name,
        "modelNumber": model,
        "tankType": tank_type,
        "tankHeight": tank_height,
        "vertical": vertical,
        "propaneButaneRatio": 1,
    }


def make_reading(
    *,
    level_m: float = 0.167,
    temp_c: float = 21,
    battery: float = 2.94,
    quality: int = 3,
    timestamp_ms: int = 1_762_000_000_000,
    source: str = "gw-0001",
) -> dict[str, Any]:
    """Return a data-shadow item with DynamoDB-typed attributes."""
    return {
        "Value": {"N": str(level_m)},
        "Timestamp": {"N": str(timestamp_ms)},
        "Temp": {"N": str(temp_c)},
        "BatteryLevel": {"N": str(battery)},
        "Quality": {"N": str(quality)},
        "Source": {"S": source},
        "ttl": {"N": str(timestamp_ms // 1000 + 90 * 86400)},
    }


class FakeGateway:
    """Fake Mopeka gateway that checks tokens against a FakeCognito."""

    def __init__(self, mocker: AiohttpClientMocker, cognito: FakeCognito) -> None:
        """Initialize with an empty account."""
        self.mocker = mocker
        self.cognito = cognito
        self.devices: list[dict[str, Any]] = []
        self.readings: dict[str, list[dict[str, Any]]] = {}
        # Token type the gateway accepts in its Auth header.
        self.accepted_token: str = "access"
        # Per-address HTTP status to return instead of the reading.
        self.reading_status: dict[str, int] = {}
        self.device_list_override: AiohttpClientMockResponse | None = None
        # Exceptions to raise instead of responding, oldest first.
        self.queued_exceptions: list[Exception] = []
        self.requests: list[tuple[str, dict[str, str]]] = []

    def add_tank(
        self, device: dict[str, Any], reading: dict[str, Any] | None = None
    ) -> None:
        """Add a device and, optionally, its latest reading."""
        self.devices.append(device)
        self.readings[device["address"]] = [reading] if reading else []

    def _respond(
        self, url: URL, status: int, payload: Any
    ) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(
            "get",
            url,
            status=status,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    async def handle(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        """Handle one request routed here by aioclient_mock."""
        headers = dict(self.mocker.mock_calls[-1][3] or {})
        self.requests.append((url.path, headers))
        if self.queued_exceptions:
            raise self.queued_exceptions.pop(0)
        token = headers.get("Auth", "")
        valid = {
            "access": self.cognito.access_tokens,
            "id": self.cognito.id_tokens,
        }.get(self.accepted_token, set())
        if token not in valid:
            return self._respond(
                url, HTTPStatus.UNAUTHORIZED, {"message": "Unauthorized"}
            )

        base_path = URL(GATEWAY_URL).path
        if url.path == base_path:
            if self.device_list_override is not None:
                return self.device_list_override
            return self._respond(
                url, HTTPStatus.OK, {"devices": deepcopy(self.devices)}
            )

        address = url.path.removeprefix(base_path + "/").removesuffix("/data-shadow")
        if status := self.reading_status.get(address):
            return self._respond(url, status, {"message": "error"})
        if address not in self.readings:
            return self._respond(url, HTTPStatus.NOT_FOUND, {"message": "Not Found"})
        items = deepcopy(self.readings[address][:1])
        return self._respond(
            url,
            HTTPStatus.OK,
            {"timeSeries": {"Items": items, "Count": len(items)}},
        )


@dataclass
class FakeCloud:
    """Both fakes, wired into aioclient_mock."""

    cognito: FakeCognito
    gateway: FakeGateway


def install_fake_cloud(mocker: AiohttpClientMocker) -> FakeCloud:
    """Route Cognito and gateway requests to new fakes and return them."""
    cognito = FakeCognito(mocker)
    gateway = FakeGateway(mocker, cognito)
    mocker.post(COGNITO_URL, side_effect=cognito.handle)
    mocker.get(
        re.compile(r"^" + re.escape(GATEWAY_URL) + r"(/|\?|$)"),
        side_effect=gateway.handle,
    )
    return FakeCloud(cognito=cognito, gateway=gateway)
