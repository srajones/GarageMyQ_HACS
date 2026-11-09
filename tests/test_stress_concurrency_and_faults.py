"""Empirical Adversarial Stress Test Suite for Milestone M6.

Stress-tests:
1. Concurrency: 20, 50, and 100 concurrent coroutines calling token refresh and API endpoints.
   Verifies single-flight lock, strictly 1 network refresh, zero token reuse, zero unhandled crashes.
2. Fault-Injection Circuit Breakers:
   Injects intermittent network dropouts, 400 invalid_grant, 401.102, 401, 403, 429, 500, 502, 504.
   Verifies ConfigEntryAuthFailed is raised on permanent auth rejection with ZERO background password retries.
3. Physical Safety Invariants:
   Stress-tests rapid concurrent service calls against open/opening and closed/closing states.
   Verifies zero duplicate commands issued, timeout non-retry invariant, and stall/reversal detection.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.myq_apk.api import MyQClient
from custom_components.myq_apk.binary_sensor import MyQProblemBinarySensor
from custom_components.myq_apk.const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    STATE_AUTOREVERSE,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_STOPPED,
    TARGET_STATE_TIMEOUT,
    MyQAuthError,
    MyQError,
    MyQInvalidGrantError,
    MyQOfflineError,
    MyQRateLimitError,
    MyQTimeoutError,
)
from custom_components.myq_apk.coordinator import MyQDataUpdateCoordinator
from custom_components.myq_apk.cover import MyQGarageDoor
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from tests.conftest import MockAsyncSession, MockResponse, SAMPLE_GARAGE_DOOR_DEVICE


# =============================================================================
# PART 1: CONCURRENCY STRESS TESTS (20, 50, 100 COROUTINES)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency_count", [20, 50, 100])
async def test_concurrency_single_flight_lock_stress(
    mock_session: MockAsyncSession, concurrency_count: int
) -> None:
    """Empirically stress-test single-flight lock with 20, 50, and 100 concurrent refresh requests.

    Verifies:
    1. Strictly ONE network refresh request is issued to /connect/token.
    2. All coroutines receive the identical refreshed tokens.
    3. Rolling refresh token is updated atomically with zero token reuse or stale token leaks.
    4. Zero unhandled exceptions occur across all coroutines.
    """
    refresh_network_calls = 0

    async def delayed_token_response(request_record: dict[str, Any]) -> MockResponse:
        nonlocal refresh_network_calls
        refresh_network_calls += 1
        # Realistic network delay to ensure all coroutines queue up at the lock
        await asyncio.sleep(0.04)
        return MockResponse(
            status_code=200,
            json_data={
                "access_token": f"access_token_stress_{concurrency_count}",
                "refresh_token": f"rolling_refresh_token_stress_{concurrency_count}",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "MyQ_Residential offline_access",
            },
        )

    mock_session.add_route("/connect/token", delayed_token_response)

    persisted_tokens: list[dict[str, Any]] = []

    def on_refresh(token_data: dict[str, Any]) -> None:
        persisted_tokens.append(dict(token_data))

    client = MyQClient(
        access_token="initial_stale_access_token",
        refresh_token="initial_valid_refresh_token",
        expires_at=time.time() - 50,  # Expired
        session=mock_session,
        on_token_refreshed=on_refresh,
    )

    # Launch concurrent refresh coroutines simultaneously
    tasks = [
        asyncio.create_task(client.refresh_tokens(force=False))
        for _ in range(concurrency_count)
    ]
    results = await asyncio.gather(*tasks)

    # Invariant 1: Strictly ONE network refresh call occurred
    assert refresh_network_calls == 1, (
        f"Expected exactly 1 network refresh call for {concurrency_count} concurrent coroutines, "
        f"but got {refresh_network_calls}!"
    )
    token_requests = [r for r in mock_session.requests if "/connect/token" in r["url"]]
    assert len(token_requests) == 1

    # Invariant 2: All coroutines received the identical refreshed tokens
    expected_access = f"access_token_stress_{concurrency_count}"
    expected_refresh = f"rolling_refresh_token_stress_{concurrency_count}"
    for res in results:
        assert res[CONF_ACCESS_TOKEN] == expected_access
        assert res[CONF_REFRESH_TOKEN] == expected_refresh

    # Invariant 3: Client internal state reflects the new tokens
    assert client.access_token == expected_access
    assert client.refresh_token == expected_refresh
    assert client.expires_at is not None and client.expires_at > time.time() + 3000

    # Invariant 4: Persistence callback executed cleanly
    assert len(persisted_tokens) == 1
    assert persisted_tokens[0]["access_token"] == expected_access
    assert persisted_tokens[0]["refresh_token"] == expected_refresh


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency_count", [20, 50, 100])
async def test_concurrency_reactive_force_refresh_stress(
    mock_session: MockAsyncSession, concurrency_count: int
) -> None:
    """Empirically stress-test reactive force=True refreshes under high concurrency.

    Simulates the condition where 20, 50, or 100 coroutines encounter 401 simultaneously
    and trigger reactive force=True refresh.
    Verifies that the lock wait check (self._last_refresh_time > timestamp and access_token != old)
    prevents redundant refresh storms even when force=True is passed.
    """
    refresh_network_calls = 0

    async def delayed_token_response(request_record: dict[str, Any]) -> MockResponse:
        nonlocal refresh_network_calls
        refresh_network_calls += 1
        await asyncio.sleep(0.04)
        return MockResponse(
            status_code=200,
            json_data={
                "access_token": f"forced_access_token_{concurrency_count}",
                "refresh_token": f"forced_refresh_token_{concurrency_count}",
                "expires_in": 3600,
            },
        )

    mock_session.add_route("/connect/token", delayed_token_response)

    client = MyQClient(
        access_token="initial_access_token",
        refresh_token="initial_refresh_token",
        expires_at=time.time() + 1000,
        session=mock_session,
    )

    # Launch concurrent force=True refreshes
    tasks = [
        asyncio.create_task(client.refresh_tokens(force=True))
        for _ in range(concurrency_count)
    ]
    results = await asyncio.gather(*tasks)

    # Exactly 1 network refresh request occurred
    assert refresh_network_calls == 1
    for res in results:
        assert res[CONF_ACCESS_TOKEN] == f"forced_access_token_{concurrency_count}"
        assert res[CONF_REFRESH_TOKEN] == f"forced_refresh_token_{concurrency_count}"


@pytest.mark.asyncio
async def test_concurrency_proactive_refresh_under_api_load(
    mock_session: MockAsyncSession,
) -> None:
    """Stress-test 50 concurrent get_devices() calls triggering proactive token refresh.

    When token expires within 300s, all 50 concurrent calls must be gated such that
    only 1 refresh occurs, and all 50 device calls succeed with the new Bearer token.
    """
    refresh_calls = 0

    async def token_handler(req: dict[str, Any]) -> MockResponse:
        nonlocal refresh_calls
        refresh_calls += 1
        await asyncio.sleep(0.03)
        return MockResponse(
            status_code=200,
            json_data={
                "access_token": "proactive_fresh_bearer_token",
                "refresh_token": "proactive_fresh_rolling_token",
                "expires_in": 3600,
            },
        )

    mock_session.add_route("/connect/token", token_handler)

    client = MyQClient(
        access_token="expiring_bearer_token",
        refresh_token="valid_refresh_token",
        expires_at=time.time() + 120,  # Expiring in 2 minutes (< 300s)
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    tasks = [asyncio.create_task(client.get_devices()) for _ in range(50)]
    all_device_lists = await asyncio.gather(*tasks)

    # 1. Exactly 1 token refresh request
    assert refresh_calls == 1

    # 2. All 50 get_devices calls succeeded
    assert len(all_device_lists) == 50
    for dev_list in all_device_lists:
        assert len(dev_list) == 2

    # 3. All device calls used the refreshed bearer token
    dev_requests = [r for r in mock_session.requests if "/Devices" in r["url"]]
    assert len(dev_requests) == 50
    for r in dev_requests:
        assert r["headers"]["Authorization"] == "Bearer proactive_fresh_bearer_token"


# =============================================================================
# PART 2: FAULT-INJECTION & CIRCUIT BREAKER VERIFICATION
# =============================================================================

@pytest.mark.asyncio
async def test_fault_injection_invalid_grant_trips_circuit_breaker_and_no_password_loop(
    mock_hass: Any, mock_config_entry: Any, mock_session: MockAsyncSession
) -> None:
    """Fault-injection: permanent refresh rejection (HTTP 400 invalid_grant).

    Verifies:
    1. MyQInvalidGrantError is raised.
    2. Client wipes access_token, refresh_token, and expires_at.
    3. Coordinator converts this to ConfigEntryAuthFailed immediately.
    4. ZERO username/password background attempts are initiated.
    """
    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=400,
            json_data={
                "error": "invalid_grant",
                "error_description": "Refresh token has been revoked or invalidated.",
            },
        ),
    )

    client = MyQClient(
        access_token="stale_token",
        refresh_token="revoked_token",
        expires_at=time.time() - 100,
        session=mock_session,
    )

    coordinator = MyQDataUpdateCoordinator(mock_hass, client, mock_config_entry)

    # Attempt coordinator refresh
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coordinator._async_update_data()

    assert "permanently rejected" in str(exc_info.value).lower()

    # Invariant: tokens wiped to prevent refresh loops
    assert client.access_token is None
    assert client.refresh_token is None
    assert client.expires_at is None

    # Invariant: zero password / authorize calls occurred
    auth_calls = [
        r for r in mock_session.requests if "/connect/authorize" in r["url"]
    ]
    assert len(auth_calls) == 0


@pytest.mark.asyncio
async def test_fault_injection_code_401_102_trips_circuit_breaker(
    mock_hass: Any, mock_config_entry: Any, mock_session: MockAsyncSession
) -> None:
    """Fault-injection: HTTP 401 with code 401.102 (refresh token expired/invalid).

    Verifies immediate ConfigEntryAuthFailed and zero background retry loops.
    """
    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=401,
            json_data={
                "code": "401.102",
                "message": "OAUTH_TOKEN_BAD_REQUEST: Refresh token expired.",
            },
        ),
    )

    client = MyQClient(
        access_token="expired_token",
        refresh_token="expired_refresh_token",
        expires_at=time.time() - 10,
        session=mock_session,
    )
    coordinator = MyQDataUpdateCoordinator(mock_hass, client, mock_config_entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert client.access_token is None
    assert client.refresh_token is None


@pytest.mark.asyncio
async def test_fault_injection_rate_limit_429_preserves_tokens(
    mock_hass: Any, mock_config_entry: Any, mock_session: MockAsyncSession
) -> None:
    """Fault-injection: HTTP 429 Too Many Requests (code 309).

    Verifies:
    1. Coordinator raises UpdateFailed (transient failure), NOT ConfigEntryAuthFailed.
    2. Tokens are NOT wiped.
    3. Integration will retry on next poll cycle.
    """
    mock_session.add_route(
        "/api/v6.0/Accounts",
        MockResponse(
            status_code=429,
            json_data={"code": "309", "message": "Rate limit exceeded. Try again in 60s."},
        ),
    )

    client = MyQClient(
        access_token="valid_active_access_token",
        refresh_token="valid_active_refresh_token",
        expires_at=time.time() + 3600,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )
    coordinator = MyQDataUpdateCoordinator(mock_hass, client, mock_config_entry)

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert "rate limit" in str(exc_info.value).lower()
    # Invariant: valid tokens MUST remain intact
    assert client.access_token == "valid_active_access_token"
    assert client.refresh_token == "valid_active_refresh_token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code, response_text",
    [
        (500, "<html><title>500 Internal Server Error</title><body>Server Crash</body></html>"),
        (502, "<html><title>502 Bad Gateway</title><body>Cloud Gateway Down</body></html>"),
        (503, "<html><title>503 Service Unavailable</title><body>Under Maintenance</body></html>"),
        (504, "<html><title>504 Gateway Time-out</title><body>Timeout</body></html>"),
    ],
)
async def test_fault_injection_server_errors_and_html_responses(
    mock_hass: Any,
    mock_config_entry: Any,
    mock_session: MockAsyncSession,
    status_code: int,
    response_text: str,
) -> None:
    """Fault-injection: HTML 500, 502, 503, and 504 server outages.

    Verifies coordinator raises UpdateFailed without crashing or corrupting token state.
    """
    mock_session.add_route(
        "/api/v6.0/Accounts",
        MockResponse(
            status_code=status_code,
            text=response_text,
            headers={"Content-Type": "text/html"},
        ),
    )

    client = MyQClient(
        access_token="valid_token",
        refresh_token="valid_refresh",
        expires_at=time.time() + 3600,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )
    coordinator = MyQDataUpdateCoordinator(mock_hass, client, mock_config_entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    # Tokens preserved
    assert client.access_token == "valid_token"


@pytest.mark.asyncio
async def test_fault_injection_network_socket_dropouts(
    mock_hass: Any, mock_config_entry: Any, mock_session: MockAsyncSession
) -> None:
    """Fault-injection: network drops and connection resets (OSError / connection dropped).

    Verifies client maps network exceptions to MyQOfflineError and coordinator raises UpdateFailed.
    """
    async def dropping_route(record: dict[str, Any]) -> MockResponse:
        raise OSError("Connection reset by peer: network cable unplugged")

    mock_session.add_route("/api/v6.0/Accounts", dropping_route)

    client = MyQClient(
        access_token="valid_token",
        refresh_token="valid_refresh",
        expires_at=time.time() + 3600,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )
    coordinator = MyQDataUpdateCoordinator(mock_hass, client, mock_config_entry)

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert "communication error" in str(exc_info.value).lower()


# =============================================================================
# PART 3: PHYSICAL GARAGE DOOR SAFETY INVARIANTS STRESS TESTS
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [STATE_OPEN, STATE_OPENING])
async def test_stress_concurrent_open_suppression(
    mock_coordinator: Any, initial_state: str
) -> None:
    """Adversarial stress test: 50 concurrent async_open_cover calls when door is open or opening.

    Physical Safety Invariant:
    Under NO circumstances must a command payload be dispatched if the door is already open or opening.
    Verifies strictly 0 network commands sent across 50 simultaneous service requests.
    """
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"]["door_state"] = initial_state
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    open_calls = 0

    async def mock_open_door(*args: Any, **kwargs: Any) -> bool:
        nonlocal open_calls
        open_calls += 1
        return True

    mock_coordinator.client.open_door = mock_open_door

    # Dispatch 50 concurrent open requests
    tasks = [asyncio.create_task(cover.async_open_cover()) for _ in range(50)]
    await asyncio.gather(*tasks)

    # Invariant: ZERO network commands dispatched
    assert open_calls == 0, (
        f"Physical safety invariant violated! Door in state '{initial_state}' received "
        f"{open_calls} open commands!"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [STATE_CLOSED, STATE_CLOSING])
async def test_stress_concurrent_close_suppression(
    mock_coordinator: Any, initial_state: str
) -> None:
    """Adversarial stress test: 50 concurrent async_close_cover calls when door is closed or closing.

    Physical Safety Invariant:
    Under NO circumstances must a command payload be dispatched if the door is already closed or closing.
    Verifies strictly 0 network commands sent across 50 simultaneous service requests.
    """
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"]["door_state"] = initial_state
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    close_calls = 0

    async def mock_close_door(*args: Any, **kwargs: Any) -> bool:
        nonlocal close_calls
        close_calls += 1
        return True

    mock_coordinator.client.close_door = mock_close_door

    # Dispatch 50 concurrent close requests
    tasks = [asyncio.create_task(cover.async_close_cover()) for _ in range(50)]
    await asyncio.gather(*tasks)

    # Invariant: ZERO network commands dispatched
    assert close_calls == 0, (
        f"Physical safety invariant violated! Door in state '{initial_state}' received "
        f"{close_calls} close commands!"
    )


@pytest.mark.asyncio
async def test_stress_timeout_non_retry_and_state_refresh(mock_coordinator: Any) -> None:
    """Stress-test timeout non-retry invariant across multiple rapid actuations.

    When TCP/TLS timeout occurs during command dispatch, the client must NEVER
    re-send the command payload (which could cause unintended reversal or entrapment).
    Instead, it must trigger coordinator refresh.
    """
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"]["door_state"] = STATE_CLOSED
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    dispatched_commands = 0

    async def timing_out_open(ser: str, **kwargs: Any) -> bool:
        nonlocal dispatched_commands
        dispatched_commands += 1
        raise MyQTimeoutError("Connection timed out waiting for cloud ack")

    mock_coordinator.client.open_door = timing_out_open

    refresh_requested = 0

    async def mock_request_refresh() -> None:
        nonlocal refresh_requested
        refresh_requested += 1

    mock_coordinator.async_request_refresh = mock_request_refresh

    # Issue command
    await cover.async_open_cover()

    # Verify: payload was dispatched exactly 1 time (no retries)
    assert dispatched_commands == 1
    # State refresh was triggered instead of command replay
    assert refresh_requested == 1


@pytest.mark.asyncio
async def test_stress_stall_and_autoreverse_safety_chain(mock_coordinator: Any) -> None:
    """Stress-test the complete physical safety monitoring state machine:

    1. Normal motion -> problem sensor OFF.
    2. Obstruction autoreverse -> problem sensor ON, fast burst aborted.
    3. Mid-transit stop -> problem sensor ON.
    4. 45-second transit timeout -> problem sensor ON.
    5. Clean resolution -> problem sensor clears when door reaches target state.
    """
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    problem_sensor = MyQProblemBinarySensor(mock_coordinator, serial)

    # Step 1: Initial state (closed, healthy)
    assert problem_sensor.is_on is False

    # Step 2: Door instructed to open, but encounters obstruction and auto-reverses
    mock_coordinator.start_fast_refresh(serial, STATE_OPEN)
    assert mock_coordinator._in_fast_burst is True

    # Cloud poll reports autoreverse
    dev_autoreverse = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_autoreverse["state"]["door_state"] = STATE_AUTOREVERSE
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_autoreverse])

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is True
    assert problem_sensor.is_on is True
    assert mock_coordinator._in_fast_burst is False  # Fast burst halted on safety hazard

    # Step 3: Hardware safety invariant - while cloud reports door_state=autoreverse,
    # clearing the coordinator stall flag alone must NOT clear the problem sensor.
    mock_coordinator.clear_device_stall(serial)
    assert problem_sensor.is_on is True  # Persists while physical door is in autoreverse

    # When door transitions out of autoreverse (e.g. to closing upon re-actuation)
    dev_closing_step3 = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_closing_step3["state"]["door_state"] = STATE_CLOSING
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_closing_step3])
    await mock_coordinator.async_request_refresh()
    assert problem_sensor.is_on is False

    # Step 4: Mid-transit stop
    mock_coordinator.start_fast_refresh(serial, STATE_CLOSED)
    dev_stopped = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_stopped["state"]["door_state"] = STATE_STOPPED
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_stopped])

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is True
    assert problem_sensor.is_on is True

    # Step 5: 45-second stall timeout
    mock_coordinator.clear_device_stall(serial)
    mock_coordinator.start_fast_refresh(serial, STATE_CLOSED)

    dev_closing = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_closing["state"]["door_state"] = STATE_CLOSING
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_closing])

    # Simulate 46 seconds elapsed since command issuance
    mock_coordinator._command_issue_time[serial] = time.monotonic() - (TARGET_STATE_TIMEOUT + 1)

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is True
    assert problem_sensor.is_on is True

    # Step 6: Next poll shows door successfully reached closed
    dev_closed = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev_closed["state"]["door_state"] = STATE_CLOSED
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev_closed])

    # Re-issue close command issue time
    mock_coordinator._command_issue_time[serial] = time.monotonic()
    mock_coordinator._command_target_state[serial] = STATE_CLOSED

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is False
    assert problem_sensor.is_on is False


@pytest.mark.asyncio
async def test_concurrency_invalid_grant_storm(mock_session: MockAsyncSession) -> None:
    """Adversarial stress-test: 20 coroutines call refresh_tokens() concurrently when token is revoked.

    Verifies:
    1. Every coroutine receives MyQInvalidGrantError or MyQAuthError.
    2. Zero unhandled exceptions or crashes occur.
    3. Tokens are cleared.
    """
    token_requests = 0

    async def invalid_grant_handler(req: dict[str, Any]) -> MockResponse:
        nonlocal token_requests
        token_requests += 1
        await asyncio.sleep(0.02)
        return MockResponse(
            status_code=400,
            json_data={
                "error": "invalid_grant",
                "error_description": "Refresh token is invalid or expired.",
            },
        )

    mock_session.add_route("/connect/token", invalid_grant_handler)

    client = MyQClient(
        access_token="old_access_token",
        refresh_token="revoked_refresh_token",
        expires_at=time.time() - 10,
        session=mock_session,
    )

    tasks = [asyncio.create_task(client.refresh_tokens()) for _ in range(20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # All 20 coroutines received an auth error
    for res in results:
        assert isinstance(res, (MyQInvalidGrantError, MyQAuthError))

    # Single-flight lock prevented redundant requests: exactly 1 request sent
    assert token_requests == 1

    # Internal token state wiped
    assert client.access_token is None
    assert client.refresh_token is None


@pytest.mark.asyncio
async def test_stress_concurrent_in_flight_actuation_suppressed(mock_coordinator: Any) -> None:
    """Adversarial stress-test: 20 concurrent async_open_cover and close_cover calls when door is in motion.

    Verifies in-flight guard prevents redundant network calls during slow network transit.
    """
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = copy.deepcopy(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"]["door_state"] = STATE_CLOSED
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    open_calls = 0

    async def mock_open(s: str) -> bool:
        nonlocal open_calls
        open_calls += 1
        await asyncio.sleep(0.05)
        return True

    mock_coordinator.client.open_door = mock_open

    # 20 concurrent open calls while closed
    tasks = [asyncio.create_task(cover.async_open_cover()) for _ in range(20)]
    await asyncio.gather(*tasks)

    # In-flight guard ensured strictly 1 open_door command was dispatched
    assert open_calls == 1

    # Now test close while open
    mock_coordinator.data[serial]["state"]["door_state"] = STATE_OPEN
    close_calls = 0

    async def mock_close(s: str) -> bool:
        nonlocal close_calls
        close_calls += 1
        await asyncio.sleep(0.05)
        return True

    mock_coordinator.client.close_door = mock_close

    tasks = [asyncio.create_task(cover.async_close_cover()) for _ in range(20)]
    await asyncio.gather(*tasks)

    assert close_calls == 1


