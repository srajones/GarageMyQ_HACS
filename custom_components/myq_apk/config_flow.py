"""Config flow and options flow for Chamberlain/LiftMaster myQ integration.

Supports Mode A (automated credentials OAuth2 + PKCE) and Mode B (manual browser redirect URL paste),
as well as re-authentication and configurable polling options. Conforms to Requirement R3.2.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import MyQClient
from .const import (
    AUTH_MODE_AUTOMATED,
    AUTH_MODE_MANUAL,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_AUTH_MODE,
    CONF_CODE_VERIFIER,
    CONF_EXPIRES_AT,
    CONF_PASSWORD,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MyQAuthError,
    MyQError,
    MyQInvalidGrantError,
    MyQOfflineError,
    MyQTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

STEP_AUTOMATED_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REDIRECT_URL): str,
    }
)


class MyQConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Chamberlain/LiftMaster myQ."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._code_verifier: Optional[str] = None
        self._auth_url: Optional[str] = None
        self._reauth_entry_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Present initial authentication mode menu to user."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["mode_automated", "mode_manual"],
        )

    async def async_step_mode_automated(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Handle Mode A: automated username/password OAuth2 + PKCE login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            client = MyQClient()

            try:
                token_data = await client.authenticate(username, password)
                accounts = await client.get_accounts()
                account_id = (
                    accounts[0]["id"]
                    if accounts and "id" in accounts[0]
                    else client.account_id or "myq_account"
                )
                account_name = (
                    accounts[0].get("name", "myQ") if accounts else "myQ"
                )
            except (MyQInvalidGrantError, MyQAuthError) as exc:
                _LOGGER.warning("Authentication failed in automated flow: %s", exc)
                errors["base"] = "invalid_auth"
            except (MyQOfflineError, MyQTimeoutError) as exc:
                _LOGGER.warning("Connection failure in automated flow: %s", exc)
                errors["base"] = "cannot_connect"
            except Exception as exc:
                _LOGGER.exception("Unexpected error in automated flow: %s", exc)
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"myQ ({account_name})",
                    data={
                        **token_data,
                        CONF_ACCOUNT_ID: account_id,
                        CONF_AUTH_MODE: AUTH_MODE_AUTOMATED,
                        CONF_USERNAME: username,
                    },
                )

        return self.async_show_form(
            step_id="mode_automated",
            data_schema=STEP_AUTOMATED_SCHEMA,
            errors=errors,
        )

    async def async_step_mode_manual(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Handle Mode B: manual browser redirect URL paste fallback."""
        errors: dict[str, str] = {}

        if not self._code_verifier or not self._auth_url:
            self._code_verifier, code_challenge = MyQClient.generate_pkce()
            client = MyQClient()
            self._auth_url = client.get_authorization_url(code_challenge)

        if user_input is not None:
            redirect_url = user_input[CONF_REDIRECT_URL].strip()
            client = MyQClient()

            try:
                token_data = await client.authenticate_with_redirect_url(
                    redirect_url, self._code_verifier
                )
                accounts = await client.get_accounts()
                account_id = (
                    accounts[0]["id"]
                    if accounts and "id" in accounts[0]
                    else client.account_id or "myq_account"
                )
                account_name = (
                    accounts[0].get("name", "myQ") if accounts else "myQ"
                )
            except (MyQInvalidGrantError, MyQAuthError) as exc:
                _LOGGER.warning("Authentication failed in manual URL flow: %s", exc)
                if "no authorization code" in str(exc).lower():
                    errors["base"] = "no_code"
                else:
                    errors["base"] = "invalid_auth"
            except (MyQOfflineError, MyQTimeoutError) as exc:
                _LOGGER.warning("Connection failure in manual URL flow: %s", exc)
                errors["base"] = "cannot_connect"
            except Exception as exc:
                _LOGGER.exception("Unexpected error in manual URL flow: %s", exc)
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"myQ ({account_name})",
                    data={
                        **token_data,
                        CONF_ACCOUNT_ID: account_id,
                        CONF_AUTH_MODE: AUTH_MODE_MANUAL,
                    },
                )

        return self.async_show_form(
            step_id="mode_manual",
            data_schema=STEP_MANUAL_SCHEMA,
            errors=errors,
            description_placeholders={"auth_url": self._auth_url},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle initiation of re-authentication flow."""
        self._reauth_entry_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm re-authentication and update credentials/tokens."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            client = MyQClient()

            try:
                token_data = await client.authenticate(username, password)
            except (MyQInvalidGrantError, MyQAuthError) as exc:
                _LOGGER.warning("Re-authentication rejected: %s", exc)
                errors["base"] = "invalid_auth"
            except (MyQOfflineError, MyQTimeoutError) as exc:
                _LOGGER.warning("Connection failure during re-authentication: %s", exc)
                errors["base"] = "cannot_connect"
            except Exception as exc:
                _LOGGER.exception("Unexpected error during re-authentication: %s", exc)
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                reauth_entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        **token_data,
                        CONF_USERNAME: username,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=self._reauth_entry_data.get(CONF_USERNAME, ""),
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )

    def _get_reauth_entry(self) -> ConfigEntry:
        """Retrieve the config entry currently undergoing re-authentication."""
        if hasattr(super(), "_get_reauth_entry"):
            return super()._get_reauth_entry()
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise ValueError(f"Config entry {entry_id} not found for reauth")
        return entry

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Get options flow handler for this config entry."""
        return MyQOptionsFlowHandler(config_entry)


class MyQOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for myQ integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Manage options for scan interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_interval,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                )
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
