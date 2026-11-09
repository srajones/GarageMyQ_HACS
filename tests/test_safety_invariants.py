"""Physical Garage Door Safety Invariant tests.

Conforms to Requirement R4 (Physical Garage Door Safety Invariants) and PROTOCOL.md §10:
1. Idempotent open gating (0 HTTP requests sent if door is already open or opening).
2. Idempotent close gating (0 HTTP requests sent if door is already closed or closing).
3. Ambiguous timeout non-retry invariant (never re-issue command payload; trigger state refresh).
4. Transition state conflict (code 321) suppression.
5. Stall, obstruction, and auto-reversal detection (problem binary sensor transitions to on).
6. 45-second command transit timeout detection.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.myq_apk.binary_sensor import MyQProblemBinarySensor
from custom_components.myq_apk.const import (
    STATE_AUTOREVERSE,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_STOPPED,
    TARGET_STATE_TIMEOUT,
    MyQTimeoutError,
)
from custom_components.myq_apk.cover import MyQGarageDoor
from tests.conftest import MockAsyncSession, MockResponse, SAMPLE_GARAGE_DOOR_DEVICE


# -----------------------------------------------------------------------------
# 1. Idempotency Invariants (Requirement R4.1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [STATE_OPEN, STATE_OPENING])
async def test_idempotent_open_suppression(mock_coordinator: Any, initial_state: str) -> None:
    """Verify async_open_cover sends ZERO requests when door is already open or opening."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = initial_state
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    # Track open_door calls
    open_called = False
    async def mock_open_door(*args: Any, **kwargs: Any) -> bool:
        nonlocal open_called
        open_called = True
        return True

    mock_coordinator.client.open_door = mock_open_door

    await cover.async_open_cover()

    # Invariant: open_door was NOT invoked
    assert open_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [STATE_CLOSED, STATE_CLOSING])
async def test_idempotent_close_suppression(mock_coordinator: Any, initial_state: str) -> None:
    """Verify async_close_cover sends ZERO requests when door is already closed or closing."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = initial_state
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    close_called = False
    async def mock_close_door(*args: Any, **kwargs: Any) -> bool:
        nonlocal close_called
        close_called = True
        return True

    mock_coordinator.client.close_door = mock_close_door

    await cover.async_close_cover()

    # Invariant: close_door was NOT invoked
    assert close_called is False


# -----------------------------------------------------------------------------
# 2. Timeout Non-Retry Invariant (Requirement R4.2)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_timeout_does_not_retry_payload(mock_coordinator: Any) -> None:
    """Verify open timeout never re-sends command payload and triggers state refresh instead."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = STATE_CLOSED
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    call_count = 0
    async def timeout_open_door(ser: str, **kwargs: Any) -> bool:
        nonlocal call_count
        call_count += 1
        raise MyQTimeoutError("TCP/TLS timeout connecting to cloud opener")

    mock_coordinator.client.open_door = timeout_open_door
    refresh_called = False
    async def mock_refresh() -> None:
        nonlocal refresh_called
        refresh_called = True

    mock_coordinator.async_request_refresh = mock_refresh

    # Must NOT raise unhandled exception and must not re-call open_door
    await cover.async_open_cover()

    assert call_count == 1  # Exactly 1 attempt, zero retries
    assert refresh_called is True  # Initiated state refresh


@pytest.mark.asyncio
async def test_close_timeout_does_not_retry_payload(mock_coordinator: Any) -> None:
    """Verify close timeout never re-sends command payload and triggers state refresh instead."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = STATE_OPEN
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)

    call_count = 0
    async def timeout_close_door(ser: str, **kwargs: Any) -> bool:
        nonlocal call_count
        call_count += 1
        raise MyQTimeoutError("Timeout closing door")

    mock_coordinator.client.close_door = timeout_close_door
    refresh_called = False
    async def mock_refresh() -> None:
        nonlocal refresh_called
        refresh_called = True

    mock_coordinator.async_request_refresh = mock_refresh

    await cover.async_close_cover()

    assert call_count == 1
    assert refresh_called is True


# -----------------------------------------------------------------------------
# 3. Transition State Conflict (Code 321) Handled Gracefully
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_conflict_code_321_handled(mock_session: MockAsyncSession) -> None:
    """Verify cloud code 321 (already in target state) returns safely without raising exception."""
    from custom_components.myq_apk.api import MyQClient

    mock_session.add_route(
        "/open",
        MockResponse(
            status_code=409,
            json_data={"code": "321", "message": "Door is already in open state."},
        ),
    )

    client = MyQClient(
        access_token="valid_token",
        account_id="acc-uuid-1234",
        session=mock_session,
    )

    # Should not raise MyQCommandError
    result = await client.open_door("CG0850123456")
    assert result is True


# -----------------------------------------------------------------------------
# 4. Stall & Auto-Reversal Detection (Requirement R4.3)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stall_detection_on_autoreverse(mock_coordinator: Any) -> None:
    """Verify safety obstruction autoreverse flags problem binary sensor to ON."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    problem_sensor = MyQProblemBinarySensor(mock_coordinator, serial)

    # Initial healthy state
    assert problem_sensor.is_on is False

    # Initiate close command
    mock_coordinator.start_fast_refresh(serial, STATE_CLOSED)

    # Simulate cloud update where door auto-reversed due to obstruction beam
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = STATE_AUTOREVERSE
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev])

    # Coordinator processes update
    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is True
    assert problem_sensor.is_on is True


@pytest.mark.asyncio
async def test_stall_detection_on_stopped_mid_travel(mock_coordinator: Any) -> None:
    """Verify door stopping mid-travel flags problem binary sensor to ON."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    problem_sensor = MyQProblemBinarySensor(mock_coordinator, serial)

    # Start open transit
    mock_coordinator.start_fast_refresh(serial, STATE_OPEN)

    # Door stopped mid-travel
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = STATE_STOPPED
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev])

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is True
    assert problem_sensor.is_on is True


@pytest.mark.asyncio
async def test_stall_detection_after_45_second_timeout(mock_coordinator: Any) -> None:
    """Verify failure to reach target state within 45 seconds flags problem condition."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    problem_sensor = MyQProblemBinarySensor(mock_coordinator, serial)

    # Issue command
    mock_coordinator.start_fast_refresh(serial, STATE_CLOSED)

    # Door stays stuck in "closing"
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = STATE_CLOSING
    mock_coordinator.client.get_devices = AsyncMock(return_value=[dev])

    # Fast forward issue time by 46 seconds (> 45s TARGET_STATE_TIMEOUT)
    mock_coordinator._command_issue_time[serial] = time.monotonic() - (TARGET_STATE_TIMEOUT + 5)

    await mock_coordinator.async_request_refresh()

    assert mock_coordinator.is_device_stalled(serial) is True
    assert problem_sensor.is_on is True

    # Stall can be explicitly cleared or cleared on new command
    mock_coordinator.clear_device_stall(serial)
    assert mock_coordinator.is_device_stalled(serial) is False
    assert problem_sensor.is_on is False
