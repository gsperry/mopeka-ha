"""Config flow for Mopeka HA."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
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
    CONF_SENSORS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)
from .coordinator import MopekaConfigEntry, update_interval_minutes
from .models import MEASUREMENT_KEYS, MopekaTank

_LOGGER = logging.getLogger(__name__)

_EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


def _sensor_choices(
    tanks: Iterable[MopekaTank], selected: Iterable[str] = ()
) -> list[SelectOptionDict]:
    """Return a choice per measurement the tanks report, with a sample value.

    Measurements no tank reports are left out, unless already selected. With no
    tank data at all, every measurement is offered.
    """
    samples: dict[str, Any] = {}
    for tank in tanks:
        for key, value in tank.values().items():
            if value is not None:
                samples.setdefault(key, value)
    keys = (
        [k for k in MEASUREMENT_KEYS if k in samples or k in set(selected)]
        if samples
        else list(MEASUREMENT_KEYS)
    )
    return [
        SelectOptionDict(
            value=key, label=f"{key} ({samples[key]})" if key in samples else key
        )
        for key in sorted(keys)
    ]


def _sensor_selector(
    tanks: Iterable[MopekaTank], selected: Iterable[str] = ()
) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=_sensor_choices(tanks, selected),
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


async def _validate(
    flow: ConfigFlow | OptionsFlow, email: str, password: str
) -> tuple[MopekaApiClient, MopekaAccountData | None, dict[str, str]]:
    """Sign in and read the account, returning the data or a form error."""
    client = MopekaApiClient(async_get_clientsession(flow.hass), email, password)
    data, errors = await _read_account(client)
    return client, data, errors


async def _read_account(
    client: MopekaApiClient,
) -> tuple[MopekaAccountData | None, dict[str, str]]:
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
    return data, {}


class MopekaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mopeka HA."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._credentials: dict[str, str] = {}
        self._account_data: MopekaAccountData | None = None

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
            client, data, errors = await _validate(
                self, email, user_input[CONF_PASSWORD]
            )
            if data is not None:
                self._credentials = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_BACKEND: str(client.backend),
                }
                self._account_data = data
                return await self.async_step_sensors()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_EMAIL): _EMAIL_SELECTOR,
                        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
                    }
                ),
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else None,
            ),
            errors=errors,
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which measurements to create as entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if selected := user_input.get(CONF_SENSORS):
                return self.async_create_entry(
                    title=self._credentials[CONF_EMAIL],
                    data=self._credentials,
                    options={
                        CONF_SENSORS: selected,
                        CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
                    },
                )
            errors["base"] = "no_sensors_selected"

        tanks = self._account_data.tanks.values() if self._account_data else ()
        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(
                {vol.Required(CONF_SENSORS, default=[]): _sensor_selector(tanks)}
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
            client, data, errors = await _validate(
                self, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if data is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BACKEND: str(client.backend),
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
            client, data, errors = await _validate(
                self, email, user_input[CONF_PASSWORD]
            )
            if data is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BACKEND: str(client.backend),
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_EMAIL): _EMAIL_SELECTOR,
                        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
                    }
                ),
                {CONF_EMAIL: entry.data[CONF_EMAIL]},
            ),
            errors=errors,
        )


class MopekaOptionsFlow(OptionsFlow):
    """Handle Mopeka HA options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the poll interval, excluded devices, and measurements."""
        entry: MopekaConfigEntry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_SENSORS):
                return self.async_create_entry(
                    data={
                        **entry.options,
                        CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                        CONF_EXCLUDED_DEVICES: user_input.get(
                            CONF_EXCLUDED_DEVICES, []
                        ),
                        CONF_SENSORS: user_input[CONF_SENSORS],
                    }
                )
            errors["base"] = "no_sensors_selected"

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
                vol.Required(
                    CONF_SENSORS, default=list(entry.options.get(CONF_SENSORS, []))
                ): _sensor_selector(
                    account.tanks.values(), entry.options.get(CONF_SENSORS, [])
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
