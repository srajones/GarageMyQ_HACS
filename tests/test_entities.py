"""Comprehensive tests for all Home Assistant entity platforms and diagnostics.

Conforms to Requirement R5 (HACS Structure & Home Assistant Entities):
1. Unified DeviceInfo across all entities for the physical opener.
2. Cover entity states, attributes, and actuation service calls.
3. Light entity states, detection, and actuation.
4. Binary sensors (PROBLEM and CONNECTIVITY).
5. Diagnostic sensors (door_status, last_event, wifi_signal, battery, session_health).
6. Button entity (Refresh Status Now).
7. Diagnostics platform redacting credentials and tokens.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.myq_apk.binary_sensor import (
    MyQConnectivityBinarySensor,
    MyQProblemBinarySensor,
)
from custom_components.myq_apk.button import MyQRefreshButton
from custom_components.myq_apk.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    STATE_AUTOREVERSE,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_STOPPED,
)
from custom_components.myq_apk.cover import MyQGarageDoor
from custom_components.myq_apk.diagnostics import async_get_config_entry_diagnostics
from custom_components.myq_apk.light import MyQWorkLight, has_light_capability
from custom_components.myq_apk.sensor import (
    MyQBatterySensor,
    MyQDoorStatusSensor,
    MyQLastEventSensor,
    MyQSessionHealthSensor,
    MyQWifiSignalSensor,
)
from tests.conftest import (
    SAMPLE_GARAGE_DOOR_DEVICE,
    SAMPLE_GATEWAY_DEVICE,
    SAMPLE_LAMP_DEVICE,
)


# -----------------------------------------------------------------------------
# 1. Unified DeviceInfo Across All Entities (Requirement R5)
# -----------------------------------------------------------------------------

def test_unified_device_info_for_opener(mock_coordinator: Any) -> None:
    """Verify all entities for the same physical opener report identical DeviceInfo."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    parent_serial = SAMPLE_GATEWAY_DEVICE["serial_number"]

    entities = [
        MyQGarageDoor(mock_coordinator, serial),
        MyQWorkLight(mock_coordinator, serial),
        MyQProblemBinarySensor(mock_coordinator, serial),
        MyQConnectivityBinarySensor(mock_coordinator, serial),
        MyQDoorStatusSensor(mock_coordinator, serial),
        MyQLastEventSensor(mock_coordinator, serial),
        MyQWifiSignalSensor(mock_coordinator, serial),
        MyQBatterySensor(mock_coordinator, serial),
        MyQSessionHealthSensor(mock_coordinator, serial, mock_coordinator.entry),
        MyQRefreshButton(mock_coordinator, serial),
    ]

    for entity in entities:
        info = entity.device_info
        assert info is not None
        assert info.identifiers == {(DOMAIN, serial)}
        assert info.manufacturer == "Chamberlain"
        assert info.via_device == (DOMAIN, parent_serial)
        assert info.sw_version == "2.45"
        assert "Main Garage Door" in str(info.get("name"))


# -----------------------------------------------------------------------------
# 2. Cover Entity Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cover_states_and_attributes(mock_coordinator: Any) -> None:
    """Verify cover entity state interpretation and attributes."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    cover = MyQGarageDoor(mock_coordinator, serial)

    # Initial state is "closed"
    assert cover.is_closed is True
    assert cover.is_opening is False
    assert cover.is_closing is False

    attrs = cover.extra_state_attributes
    assert attrs["door_state"] == "closed"
    assert attrs["is_unattended_open_allowed"] is True
    assert attrs["is_unattended_close_allowed"] is True
    assert attrs["active_fault_codes"] == []
    assert attrs["stalled"] is False

    # State: opening
    mock_coordinator.data[serial]["state"]["door_state"] = STATE_OPENING
    assert cover.is_closed is False
    assert cover.is_opening is True
    assert cover.is_closing is False

    # State: open
    mock_coordinator.data[serial]["state"]["door_state"] = STATE_OPEN
    assert cover.is_closed is False
    assert cover.is_opening is False
    assert cover.is_closing is False

    # State: closing
    mock_coordinator.data[serial]["state"]["door_state"] = STATE_CLOSING
    assert cover.is_closed is False
    assert cover.is_opening is False
    assert cover.is_closing is True


@pytest.mark.asyncio
async def test_cover_actuation_service_calls(mock_coordinator: Any) -> None:
    """Verify async_open_cover and async_close_cover issue commands to client."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    cover = MyQGarageDoor(mock_coordinator, serial)

    # Open door from closed
    mock_coordinator.data[serial]["state"]["door_state"] = STATE_CLOSED
    mock_coordinator.client.open_door = AsyncMock(return_value=True)

    await cover.async_open_cover()
    mock_coordinator.client.open_door.assert_called_once_with(serial)

    # Close door from open
    mock_coordinator.data[serial]["state"]["door_state"] = STATE_OPEN
    mock_coordinator.client.close_door = AsyncMock(return_value=True)

    await cover.async_close_cover()
    mock_coordinator.client.close_door.assert_called_once_with(serial)


# -----------------------------------------------------------------------------
# 3. Light Entity Tests
# -----------------------------------------------------------------------------

def test_has_light_capability() -> None:
    """Verify has_light_capability detects lamp modules and work-light openers."""
    assert has_light_capability(SAMPLE_LAMP_DEVICE) is True
    assert has_light_capability(SAMPLE_GARAGE_DOOR_DEVICE) is True

    # Device with explicit lamp_state
    custom_dev = {"state": {"lamp_state": "Off"}}
    assert has_light_capability(custom_dev) is True

    # Device without lights
    gateway_dev = dict(SAMPLE_GATEWAY_DEVICE)
    assert has_light_capability(gateway_dev) is False


@pytest.mark.asyncio
async def test_light_entity_operations(mock_coordinator: Any) -> None:
    """Verify light entity state reporting and turn on/off commands."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    light = MyQWorkLight(mock_coordinator, serial)

    # Initial state: lamp_state absent -> False
    assert light.is_on is False

    # State ON
    mock_coordinator.data[serial]["state"]["lamp_state"] = "On"
    assert light.is_on is True

    # State OFF
    mock_coordinator.data[serial]["state"]["lamp_state"] = "Off"
    assert light.is_on is False

    # Actuation: turn ON
    mock_coordinator.client.set_light_state = AsyncMock(return_value=True)
    await light.async_turn_on()
    mock_coordinator.client.set_light_state.assert_called_once_with(serial, True)

    # Actuation: turn OFF
    mock_coordinator.client.set_light_state = AsyncMock(return_value=True)
    await light.async_turn_off()
    mock_coordinator.client.set_light_state.assert_called_once_with(serial, False)


# -----------------------------------------------------------------------------
# 4. Binary Sensor Tests
# -----------------------------------------------------------------------------

def test_problem_binary_sensor_conditions(mock_coordinator: Any) -> None:
    """Verify problem binary sensor detects faults, communication errors, and low battery."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQProblemBinarySensor(mock_coordinator, serial)
    state = mock_coordinator.data[serial]["state"]

    # All clear initially
    assert sensor.is_on is False

    # 1. Stopped / Auto-reverse
    state["door_state"] = STATE_STOPPED
    assert sensor.is_on is True
    state["door_state"] = STATE_CLOSED

    # 2. DPS low battery mode
    state["dps_low_battery_mode"] = True
    assert sensor.is_on is True
    state["dps_low_battery_mode"] = False

    # 3. Sensor communication error
    state["sensor_comm_error"] = True
    assert sensor.is_on is True
    state["sensor_comm_error"] = False

    # 4. Active fault codes
    state["active_fault_codes"] = [{"code": "310", "description": "Obstruction"}]
    assert sensor.is_on is True
    state["active_fault_codes"] = []

    # 5. Work light fault
    state["attached_work_light_error_present"] = True
    assert sensor.is_on is True
    state["attached_work_light_error_present"] = False

    # Reverts to False
    assert sensor.is_on is False


def test_connectivity_binary_sensor(mock_coordinator: Any) -> None:
    """Verify connectivity sensor reports cloud reachability."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQConnectivityBinarySensor(mock_coordinator, serial)

    # Online initially
    assert sensor.is_on is True

    # Cloud update failed
    mock_coordinator.last_update_success = False
    assert sensor.is_on is False
    mock_coordinator.last_update_success = True

    # Device offline in cloud
    mock_coordinator.data[serial]["state"]["online"] = False
    assert sensor.is_on is False


# -----------------------------------------------------------------------------
# 5. Diagnostic Sensors & Button Tests
# -----------------------------------------------------------------------------

def test_door_status_sensor(mock_coordinator: Any) -> None:
    """Verify door status sensor reports current state string."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQDoorStatusSensor(mock_coordinator, serial)

    assert sensor.native_value == "closed"
    mock_coordinator.data[serial]["state"]["door_state"] = "opening"
    assert sensor.native_value == "opening"


def test_last_event_sensor(mock_coordinator: Any) -> None:
    """Verify last event sensor parses ISO UTC timestamps."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQLastEventSensor(mock_coordinator, serial)

    parsed_dt = sensor.native_value
    assert isinstance(parsed_dt, datetime)
    assert parsed_dt.year == 2026
    assert parsed_dt.month == 11
    assert parsed_dt.day == 8
    assert parsed_dt.hour == 22
    assert parsed_dt.minute == 45


def test_wifi_signal_sensor(mock_coordinator: Any) -> None:
    """Verify Wi-Fi signal sensor reports dBm integer or parent fallback."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQWifiSignalSensor(mock_coordinator, serial)

    assert sensor.native_value == -65
    assert sensor.native_unit_of_measurement == "dBm"

    # Gateway string rating
    gw_serial = SAMPLE_GATEWAY_DEVICE["serial_number"]
    gw_sensor = MyQWifiSignalSensor(mock_coordinator, gw_serial)
    assert gw_sensor.native_value == "Excellent"
    assert gw_sensor.native_unit_of_measurement is None


def test_battery_sensor(mock_coordinator: Any) -> None:
    """Verify battery backup sensor returns percentage and voltage."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQBatterySensor(mock_coordinator, serial)

    assert sensor.native_value == 95
    assert sensor.native_unit_of_measurement == "%"
    attrs = sensor.extra_state_attributes
    assert attrs["battery_voltage"] == "12.6"
    assert attrs["battery_backup_state"] == "Charging"


def test_session_health_sensor(mock_coordinator: Any) -> None:
    """Verify session health sensor reports token status."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    sensor = MyQSessionHealthSensor(mock_coordinator, serial, mock_coordinator.entry)

    assert sensor.native_value == "healthy"

    # Token expiring soon (< 300s)
    mock_coordinator.client.expires_at = time.time() + 100
    assert sensor.native_value == "expiring_soon"

    # Token expired
    mock_coordinator.client.expires_at = time.time() - 10
    assert sensor.native_value == "expired"


@pytest.mark.asyncio
async def test_button_entity_press(mock_coordinator: Any) -> None:
    """Verify pressing 'Refresh Status Now' button forces coordinator refresh."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    button = MyQRefreshButton(mock_coordinator, serial)

    mock_coordinator.async_request_refresh = AsyncMock()
    await button.async_press()
    mock_coordinator.async_request_refresh.assert_called_once()


# -----------------------------------------------------------------------------
# 6. Diagnostics Redaction (Requirement R5)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_keys(mock_hass: Any, mock_coordinator: Any) -> None:
    """Verify diagnostics platform redacts access_token, refresh_token, password, username, account_id."""
    entry = mock_coordinator.entry
    mock_hass.data.setdefault(DOMAIN, {})[entry.entry_id] = mock_coordinator

    diagnostics = await async_get_config_entry_diagnostics(mock_hass, entry)

    entry_data = diagnostics["entry"]["data"]
    # All sensitive keys must be replaced with **REDACTED**
    assert entry_data[CONF_ACCESS_TOKEN] == "**REDACTED**"
    assert entry_data[CONF_REFRESH_TOKEN] == "**REDACTED**"
    assert entry_data[CONF_USERNAME] == "**REDACTED**"
    assert entry_data[CONF_ACCOUNT_ID] == "**REDACTED**"

    # Non-sensitive structure remains intact
    assert diagnostics["entry"]["title"] == "myQ (Home Garage)"
    assert diagnostics["devices_count"] >= 1
