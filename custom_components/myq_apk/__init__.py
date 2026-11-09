"""Home Assistant Custom Integration for Chamberlain/LiftMaster myQ.

Conforms to Requirement R3 (anti-lockout architecture, self-healing OAuth2,
atomic token persistence) and Requirement R5 (HACS custom integration platforms).
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import MyQClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    MyQAuthError,
    MyQInvalidGrantError,
)
from .coordinator import MyQDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final[list[Platform]] = [
    Platform.COVER,
    Platform.LIGHT,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up myQ integration from a config entry."""
    access_token = entry.data.get(CONF_ACCESS_TOKEN)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    expires_at = entry.data.get(CONF_EXPIRES_AT)
    account_id = entry.data.get(CONF_ACCOUNT_ID)

    if not refresh_token and not access_token:
        raise ConfigEntryAuthFailed("No access or refresh token found in config entry")

    client = MyQClient(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        account_id=account_id,
    )

    coordinator = MyQDataUpdateCoordinator(hass, client=client, entry=entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await client.close()
        raise
    except Exception as exc:
        await client.close()
        raise ConfigEntryNotReady(f"Failed to connect to myQ cloud: {exc}") from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register listener for option updates (e.g., scan_interval changes)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Forward entry setups to all supported entity platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a myQ config entry and close HTTP sessions."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: MyQDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).pop(
            entry.entry_id, None
        )
        if coordinator is not None and coordinator.client is not None:
            await coordinator.client.close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are modified."""
    await hass.config_entries.async_reload(entry.entry_id)
