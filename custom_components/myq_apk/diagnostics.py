"""Diagnostics support for Chamberlain/LiftMaster myQ integration.

Conforms to Requirement R5: Redacting tokens, passwords, usernames, and account IDs.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

try:
    from homeassistant.components.diagnostics import async_redact_data
except ImportError:
    async_redact_data = None  # type: ignore[assignment]

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_CODE_VERIFIER,
    CONF_PASSWORD,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import MyQDataUpdateCoordinator

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_ACCOUNT_ID,
    CONF_CODE_VERIFIER,
    CONF_REDIRECT_URL,
    "access_token",
    "refresh_token",
    "password",
    "username",
    "email",
    "account_id",
    "accountId",
    "code",
    "code_verifier",
    "created_by",
}


def _redact_data(data: Any, to_redact: set[str]) -> Any:
    """Recursively redact sensitive keys from dictionary or list structure."""
    lower_redact = {k.lower() for k in to_redact}
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            if str(key).lower() in lower_redact:
                redacted[key] = "**REDACTED**"
            elif isinstance(value, (dict, list)):
                redacted[key] = _redact_data(value, to_redact)
            else:
                redacted[key] = value
        return redacted
    if isinstance(data, list):
        return [_redact_data(item, to_redact) for item in data]
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry with credentials and identifiers redacted."""
    coordinator: MyQDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )

    client_info: dict[str, Any] = {}
    coord_data: dict[str, Any] = {}

    if coordinator is not None:
        client = coordinator.client
        client_info = {
            "is_token_expiring": client.is_token_expiring(leeway=0),
            "is_token_expiring_soon": client.is_token_expiring(leeway=300),
            "has_access_token": bool(client.access_token),
            "has_refresh_token": bool(client.refresh_token),
            "expires_at": client.expires_at,
            "stalled_devices": list(coordinator._stalled_devices),
            "in_fast_burst": coordinator._in_fast_burst,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        }
        coord_data = coordinator.data

    raw_diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "client": client_info,
        "coordinator_devices": coord_data,
        "devices_count": len(coord_data),
    }

    if async_redact_data is not None:
        return async_redact_data(raw_diagnostics, TO_REDACT)

    return _redact_data(raw_diagnostics, TO_REDACT)
