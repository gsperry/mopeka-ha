"""Tests for the Mopeka HA config and options flows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mopeka_ha.const import (
    BACKEND_LEGACY,
    CONF_BACKEND,
    CONF_EXCLUDED_DEVICES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

from .conftest import ADDRESS_A, ADDRESS_B, EMAIL, PASSWORD
from .fake_cloud import FakeCloud

USER_INPUT = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}


def _choices(result: dict[str, Any], field: str) -> dict[str, str]:
    """Return {value: label} for a select selector in a form result."""
    for key, selector in result["data_schema"].schema.items():
        if key == field:
            return {o["value"]: o["label"] for o in selector.config["options"]}
    raise AssertionError(f"{field} not in form")


async def test_user_flow(hass: HomeAssistant, fake_cloud: FakeCloud) -> None:
    """Signing in creates the entry and its entities."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: f"  {EMAIL} ", CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {**USER_INPUT, CONF_BACKEND: BACKEND_LEGACY}
    assert result["options"] == {CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL}
    entry = result["result"]
    assert entry.unique_id == EMAIL
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert hass.states.get("sensor.tank_a_fill_level") is not None


def _wrong_password(cloud: FakeCloud) -> None:
    cloud.cognito.users[EMAIL].password = "something-else"


def _throttled(cloud: FakeCloud) -> None:
    cloud.cognito.queue_error("TooManyRequestsException", "Rate exceeded")


def _offline(cloud: FakeCloud) -> None:
    cloud.cognito.queue(aiohttp.ClientConnectionError("Cannot connect to host"))


def _gateway_down(cloud: FakeCloud) -> None:
    cloud.gateway.queued_exceptions.append(TimeoutError())


def _unknown_cognito_error(cloud: FakeCloud) -> None:
    cloud.cognito.queue_error(
        "ResourceNotFoundException", "User pool client does not exist"
    )


@pytest.mark.parametrize(
    ("break_cloud", "error", "log_text"),
    [
        (_wrong_password, "invalid_auth", "NotAuthorizedException"),
        (_throttled, "cannot_connect", "TooManyRequestsException"),
        (_offline, "cannot_connect", "Cannot connect to host"),
        (_gateway_down, "cannot_connect", "Timed out contacting Mopeka Cloud"),
        (_unknown_cognito_error, "unknown", "ResourceNotFoundException"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    fake_cloud: FakeCloud,
    caplog: pytest.LogCaptureFixture,
    break_cloud: Callable[[FakeCloud], None],
    error: str,
    log_text: str,
) -> None:
    """Each failure shows its own error, logs the reason, and can be retried."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    original_password = fake_cloud.cognito.users[EMAIL].password
    break_cloud(fake_cloud)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert log_text in caplog.text
    assert PASSWORD not in caplog.text

    fake_cloud.cognito.users[EMAIL].password = original_password
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_exception(
    hass: HomeAssistant, fake_cloud: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    """A bug in the client shows 'unknown' and logs a traceback."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.mopeka_ha.config_flow.MopekaApiClient.get_account_data",
        side_effect=RuntimeError("bug"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "unknown"}
    assert "Traceback" in caplog.text


async def test_user_flow_already_configured(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """The same account, in any letter case, cannot be added twice."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL.upper(), CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert fake_cloud.cognito.requests == []


async def test_reauth(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Re-authentication stores the new password after checking it."""
    fake_cloud.cognito.users[EMAIL].password = "new-password"
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_EMAIL] == EMAIL


async def test_reconfigure(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Reconfigure updates the sign-in details of the same account."""
    fake_cloud.cognito.users[EMAIL].password = "new-password"
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL.title(), CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {
        CONF_EMAIL: EMAIL.title(),
        CONF_PASSWORD: "new-password",
        CONF_BACKEND: BACKEND_LEGACY,
    }


async def test_reconfigure_other_account(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Reconfigure cannot switch the entry to a different account."""
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "other@example.com", CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_EMAIL] == EMAIL


async def test_options_flow(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Options change the interval and the excluded tanks."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    device_registry = dr.async_get(hass)
    assert device_registry.async_get_device_by_identifier(
        (DOMAIN, ADDRESS_B), config_entry.entry_id
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"
    assert _choices(result, CONF_EXCLUDED_DEVICES) == {
        ADDRESS_A: "Tank A",
        ADDRESS_B: "Tank B",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_UPDATE_INTERVAL: 15.0, CONF_EXCLUDED_DEVICES: [ADDRESS_B]},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_UPDATE_INTERVAL: 15,
        CONF_EXCLUDED_DEVICES: [ADDRESS_B],
    }
    # The entry reloads without Tank B.
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, ADDRESS_B), config_entry.entry_id
        )
        is None
    )
    assert hass.states.get("sensor.tank_a_fill_level") is not None
    assert hass.states.get("sensor.tank_b_fill_level") is None
    assert config_entry.runtime_data.update_interval.total_seconds() == 15 * 60


@pytest.mark.parametrize(
    "entry_options",
    [{CONF_UPDATE_INTERVAL: 5, CONF_EXCLUDED_DEVICES: ["GONE:00"]}],
)
async def test_options_flow_entry_not_loaded(
    hass: HomeAssistant, fake_cloud: FakeCloud, config_entry: MockConfigEntry
) -> None:
    """Options still open when the entry failed to load, keeping old exclusions."""
    fake_cloud.cognito.users[EMAIL].password = "changed"
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert _choices(result, CONF_EXCLUDED_DEVICES) == {"GONE:00": "GONE:00"}
