"""Guard against tests reaching the real Mopeka or AWS services.

pytest-homeassistant-custom-component also fails any test that opens a
socket, so a test that tried to reach the network would fail on its own.
"""

from __future__ import annotations

import socket

import pytest


@pytest.mark.parametrize(
    "host",
    ["gateway.mopeka.cloud", "dev.mopeka.cloud", "cognito-idp.us-east-1.amazonaws.com"],
)
def test_name_resolution_is_blocked(host: str) -> None:
    """Looking up Mopeka's or Amazon's hosts fails in tests."""
    with pytest.raises(RuntimeError, match="DNS resolution disabled"):
        socket.getaddrinfo(host, 443)
