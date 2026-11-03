"""Config flow for Mopeka HA."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    MopekaAccountData,
    MopekaApiClient,
    MopekaApiError,
    MopekaAuthError,
    MopekaConnectionError,
)
from .const import (
    CONF_BACKEND,
    CONF_EXCLUDED_DEVICES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)
from .coordinator import MopekaConfigEntry, update_interval_minutes

_LOGGER = logging.getLogger(__name__)

_EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): _EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
    }
)


async def _validate(
    hass: HomeAssistant, email: str, password: str
) -> tuple[str | None, dict[str, str]]:
    """Sign in and read the account.

    Return the platform holding the account, or None and a form error.
    """
    client = MopekaApiClient(async_get_clientsession(hass), email, password)
    try:
        data = await client.get_account_data()
    except MopekaAuthError as err:
        _LOGGER.warning("Mopeka sign-in was rejected: %s", err)
        return None, {"base": "invalid_auth"}
    except MopekaConnectionError as err:
        _LOGGER.warning("Cannot connect to Mopeka Cloud: %s", err)
        return None, {"base": "cannot_connect"}
    except MopekaApiError as err:
        _LOGGER.error("Unexpected response from Mopeka Cloud: %s", err)
        return None, {"base": "unknown"}
    except Exception:
        _LOGGER.exception("Unexpected error while checking the Mopeka account")
        return None, {"base": "unknown"}
    _LOGGER.debug(
        "Mopeka account on the %s platform has %d device(s), %d with readings",
        client.backend,
        len(data.devices),
        len(data.tanks),
    )
    return client.backend, {}


class MopekaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mopeka HA."""

    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: MopekaConfigEntry) -> MopekaOptionsFlow:
        """Return the options flow."""
        return MopekaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the account email and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            backend, errors = await _validate(
                self.hass, email, user_input[CONF_PASSWORD]
            )
            if backend is not None:
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BACKEND: backend,
                    },
                    options={CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _CREDENTIALS_SCHEMA,
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else None,
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after Mopeka rejected the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            backend, errors = await _validate(
                self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if backend is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BACKEND: backend,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the email address or password of the configured account."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            backend, errors = await _validate(
                self.hass, email, user_input[CONF_PASSWORD]
            )
            if backend is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BACKEND: backend,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _CREDENTIALS_SCHEMA, {CONF_EMAIL: entry.data[CONF_EMAIL]}
            ),
            errors=errors,
        )


class MopekaOptionsFlow(OptionsFlow):
    """Handle Mopeka HA options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the poll interval and the tanks to leave out."""
        entry: MopekaConfigEntry = self.config_entry
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **entry.options,
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_EXCLUDED_DEVICES: user_input.get(CONF_EXCLUDED_DEVICES, []),
                }
            )

        account = (
            entry.runtime_data.data
            if entry.state is ConfigEntryState.LOADED
            else MopekaAccountData()
        )
        excluded: list[str] = list(entry.options.get(CONF_EXCLUDED_DEVICES, []))
        device_choices = [
            SelectOptionDict(value=address, label=device.display_name)
            for address, device in sorted(
                account.devices.items(), key=lambda item: item[1].display_name
            )
        ]
        # Keep exclusions for devices that are no longer on the account.
        device_choices += [
            SelectOptionDict(value=address, label=address)
            for address in excluded
            if address not in account.devices
        ]

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=update_interval_minutes(entry.options),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL,
                        max=MAX_UPDATE_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(CONF_EXCLUDED_DEVICES, default=excluded): SelectSelector(
                    SelectSelectorConfig(
                        options=device_choices,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
