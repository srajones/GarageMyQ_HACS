"""Tests for MyQDataUpdateCoordinator.

Conforms to Requirement R3 (anti-lockout, human-like polling cadence with jitter,
fast-refresh burst post-command, atomic rolling token persistence) and PROTOCOL.md §5 & §10:
1. Jittered polling calculation (180s ± 20s, bounded 60s-600s).
2. Fast-refresh burst post-command (5s polling for up to 35s).
3. Account ID caching (prevents redundant discovery calls).
4. Atomic rolling token persistence to config_entry.data.
5. Circuit breaker exception translation (ConfigEntryAuthFailed vs UpdateFailed).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.myq_apk.api import MyQClient
from custom_components.myq_apk.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_JITTER,
    FAST_POLL_BURST_MAX,
    FAST_POLL_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    MyQAuthError,
    MyQInvalidGrantError,
    MyQOfflineError,
    MyQRateLimitError,
    MyQTimeoutError,
)
from custom_components.myq_apk.coordinator import MyQDataUpdateCoordinator
from tests.conftest import MockAsyncSession, MockResponse, SAMPLE_GARAGE_DOOR_DEVICE


# -----------------------------------------------------------------------------
# 1. Jittered Polling Calculation (Requirement R3.5)
# -----------------------------------------------------------------------------

def test_jittered_polling_calculation_default(mock_coordinator: Any) -> None:
    """Verify default scan interval (180s) includes ±20s random jitter [160, 200]."""
    samples = [mock_coordinator._calculate_jittered_interval() for _ in range(100)]

    assert all(160 <= s <= 200 for s in samples)
    # Verify randomness (not static 180)
    assert len(set(samples)) > 10


def test_jittered_polling_clamped_to_min(mock_coordinator: Any) -> None:
    """Verify options interval at minimum (60s) is clamped so it never drops below 60s."""
    mock_coordinator.entry.options[CONF_SCAN_INTERVAL] = 60

    samples = [mock_coordinator._calculate_jittered_interval() for _ in range(50)]
    assert all(s >= MIN_SCAN_INTERVAL for s in samples)
    assert all(s <= (60 + DEFAULT_SCAN_JITTER) for s in samples)


def test_jittered_polling_clamped_to_max(mock_coordinator: Any) -> None:
    """Verify options interval at maximum (600s) is clamped so it never exceeds 600s."""
    mock_coordinator.entry.options[CONF_SCAN_INTERVAL] = 600

    samples = [mock_coordinator._calculate_jittered_interval() for _ in range(50)]
    assert all(s <= MAX_SCAN_INTERVAL for s in samples)
    assert all(s >= (600 - DEFAULT_SCAN_JITTER) for s in samples)


# -----------------------------------------------------------------------------
# 2. Fast-Refresh Burst Post-Command (Requirement R3.5 & PROTOCOL.md §10.4)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fast_refresh_burst_lifecycle(mock_coordinator: Any) -> None:
    """Verify 5-second polling burst initiates post-command and reverts when target state reached."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]

    # Initial normal polling interval (approx 180s)
    assert mock_coordinator.update_interval.total_seconds() >= 160

    # Command issued to close door
    mock_coordinator.start_fast_refresh(serial, STATE_CLOSED)

    # 1. Immediate interval switches to 5 seconds
    assert mock_coordinator._in_fast_burst is True
    assert mock_coordinator.update_interval == timedelta(seconds=FAST_POLL_INTERVAL)

    # 2. Door in transit ("closing"): fast burst continues
    dev_closing = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_closing["state"] = dict(dev_closing["state"])
    dev_closing["state"]["door_state"] = STATE_CLOSING
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_closing])

    await mock_coordinator.async_request_refresh()
    assert mock_coordinator._in_fast_burst is True
    assert mock_coordinator.update_interval == timedelta(seconds=FAST_POLL_INTERVAL)

    # 3. Door reaches target state ("closed"): fast burst terminates
    dev_closed = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_closed["state"] = dict(dev_closed["state"])
    dev_closed["state"]["door_state"] = STATE_CLOSED
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_closed])

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator._in_fast_burst is False
    assert mock_coordinator.update_interval.total_seconds() >= 160


@pytest.mark.asyncio
async def test_fast_refresh_burst_timeout_reverts_to_normal(mock_coordinator: Any) -> None:
    """Verify fast-refresh burst reverts to normal polling after 35s even if target state not reached."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    mock_coordinator.start_fast_refresh(serial, STATE_CLOSED)
    assert mock_coordinator._in_fast_burst is True

    # Fast forward beyond burst max duration (35s)
    mock_coordinator._fast_poll_end_time = time.monotonic() - 1

    # Keep door in "closing"
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = STATE_CLOSING
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev])

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator._in_fast_burst is False
    assert mock_coordinator.update_interval.total_seconds() >= 160


# -----------------------------------------------------------------------------
# 3. Account ID Caching (Requirement R3.5 & PROTOCOL.md §5.1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_id_cached(mock_session: MockAsyncSession) -> None:
    """Verify get_account_id() reuses cached account ID without issuing redundant network requests."""
    client = MyQClient(
        access_token="valid_token",
        account_id="cached-uuid-1234",
        session=mock_session,
    )

    acc_id = await client.get_account_id()
    assert acc_id == "cached-uuid-1234"

    # Zero HTTP requests should have been made to /accounts
    account_requests = [r for r in mock_session.requests if "/accounts" in r["url"]]
    assert len(account_requests) == 0


@pytest.mark.asyncio
async def test_account_id_discovery_and_caching(mock_session: MockAsyncSession) -> None:
    """Verify get_account_id() queries cloud once when not pre-cached, then caches result."""
    client = MyQClient(
        access_token="valid_token",
        account_id=None,
        session=mock_session,
    )

    acc_id1 = await client.get_account_id()
    assert acc_id1 == "e36e6761-0000-4000-8000-0123456789ab"

    acc_id2 = await client.get_account_id()
    assert acc_id2 == acc_id1

    # Only 1 request was issued
    account_requests = [r for r in mock_session.requests if "/accounts" in r["url"]]
    assert len(account_requests) == 1


# -----------------------------------------------------------------------------
# 4. Atomic Rolling Token Persistence (Requirement R3.3)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_atomic_token_persistence_to_config_entry(mock_coordinator: Any) -> None:
    """Verify token refresh automatically and atomically updates ConfigEntry.data."""
    client = mock_coordinator.client
    entry = mock_coordinator.entry

    new_token_payload = {
        "access_token": "rolling_access_token_v2",
        "refresh_token": "rolling_refresh_token_v2",
        "expires_in": 3600,
    }

    # Simulate token response from server
    await client._apply_token_response(new_token_payload)

    # ConfigEntry data was immediately updated
    assert entry.data[CONF_ACCESS_TOKEN] == "rolling_access_token_v2"
    assert entry.data[CONF_REFRESH_TOKEN] == "rolling_refresh_token_v2"
    assert entry.data[CONF_EXPIRES_AT] > time.time()


# -----------------------------------------------------------------------------
# 5. Circuit Breaker Exception Translation (Requirement R3.1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coordinator_handles_invalid_grant_as_auth_failed(mock_coordinator: Any) -> None:
    """Verify permanent token rejection raises ConfigEntryAuthFailed to trigger HA reauth flow."""
    mock_coordinator.client.get_devices = AsyncMock(
        side_effect=MyQInvalidGrantError("OAuth refresh token revoked (invalid_grant)")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await mock_coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_handles_rate_limit_as_update_failed(mock_coordinator: Any) -> None:
    """Verify HTTP 429 raises UpdateFailed (transient failure), NOT ConfigEntryAuthFailed."""
    mock_coordinator.client.get_devices = AsyncMock(
        side_effect=MyQRateLimitError("Rate limit 429 exceeded")
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await mock_coordinator._async_update_data()

    assert "rate limited" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_coordinator_handles_offline_as_update_failed(mock_coordinator: Any) -> None:
    """Verify gateway communication failure raises UpdateFailed."""
    mock_coordinator.client.get_devices = AsyncMock(
        side_effect=MyQOfflineError("Device communication error 502")
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await mock_coordinator._async_update_data()

    assert "communication error" in str(exc_info.value).lower()
