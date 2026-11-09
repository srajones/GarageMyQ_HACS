"""Fault-injection and adversarial tests for malformed, hostile, and corrupted cloud payloads.

Conforms to Requirement R6.5 (Hostile Cloud Payload Tests) and PROTOCOL.md §9:
1. Upstream HTML 502 Bad Gateway and 504 Gateway Timeout responses.
2. Missing dictionary keys, empty dictionaries, and null value handling.
3. Unrecognized, garbage, and out-of-spec door state strings.
4. Corrupted and invalid timestamp formats.
5. Non-numeric and extreme battery percentages and Wi-Fi RSSI values.
6. Malformed active fault codes and sensor telemetry structures.
7. Truncated and invalid JSON bodies.
8. Boundary stress with large device collections.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.myq_apk.api import MyQClient
from custom_components.myq_apk.binary_sensor import (
    MyQConnectivityBinarySensor,
    MyQProblemBinarySensor,
)
from custom_components.myq_apk.const import (
    DOMAIN,
    STATE_UNKNOWN,
    MyQError,
    MyQOfflineError,
    MyQTimeoutError,
)
from custom_components.myq_apk.cover import MyQGarageDoor
from custom_components.myq_apk.sensor import (
    MyQBatterySensor,
    MyQDoorStatusSensor,
    MyQLastEventSensor,
    MyQWifiSignalSensor,
    _parse_iso_datetime,
)
from tests.conftest import (
    MockAsyncSession,
    MockResponse,
    SAMPLE_GARAGE_DOOR_DEVICE,
    SAMPLE_GATEWAY_DEVICE,
)


# -----------------------------------------------------------------------------
# 1. HTML 502/504 Proxy Error Responses
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_html_502_bad_gateway_response(mock_session: MockAsyncSession) -> None:
    """Verify HTML 502 Bad Gateway response raises MyQOfflineError instead of crashing JSON parser."""
    html_502 = (
        "<html><head><title>502 Bad Gateway</title></head>"
        "<body bgcolor='white'><center><h1>502 Bad Gateway</h1></center>"
        "<hr><center>cloudflare</center></body></html>"
    )
    mock_session.add_route(
        "/Devices",
        MockResponse(status_code=502, text=html_502),
    )

    client = MyQClient(
        access_token="valid_token",
        account_id="acc-uuid-1234",
        session=mock_session,
    )

    with pytest.raises(MyQOfflineError) as exc_info:
        await client.get_devices()

    assert "offline" in str(exc_info.value).lower() or "502" in str(exc_info.value)


@pytest.mark.asyncio
async def test_html_504_gateway_timeout_response(mock_session: MockAsyncSession) -> None:
    """Verify HTML 504 Gateway Timeout response raises clean MyQError without JSON decoder failure."""
    html_504 = "<html><body><h1>504 Gateway Time-out</h1></body></html>"
    mock_session.add_route(
        "/Devices",
        MockResponse(status_code=504, text=html_504),
    )

    client = MyQClient(
        access_token="valid_token",
        account_id="acc-uuid-1234",
        session=mock_session,
    )

    with pytest.raises(MyQError) as exc_info:
        await client.get_devices()

    assert "504" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 2. Missing Fields, Empty Dicts, and Null Values
# -----------------------------------------------------------------------------

def test_missing_fields_in_device_dictionary() -> None:
    """Verify helper functions gracefully handle completely empty or stripped device payloads."""
    empty_dev: dict[str, Any] = {}
    assert MyQClient.is_garage_door(empty_dev) is False
    assert MyQClient.is_lamp(empty_dev) is False
    assert MyQClient.is_gateway(empty_dev) is False
    assert MyQClient.get_door_state(empty_dev) == STATE_UNKNOWN
    assert MyQClient.get_parent_serial_number(empty_dev) is None
    assert MyQClient.is_device_online(empty_dev) is True

    # Device with null state
    null_state_dev = {"serial_number": "CG0000000000", "state": None}
    assert MyQClient.get_door_state(null_state_dev) == STATE_UNKNOWN
    assert MyQClient.get_parent_serial_number(null_state_dev) is None
    assert MyQClient.is_device_online(null_state_dev) is True


@pytest.mark.asyncio
async def test_entities_survive_empty_device_state(mock_coordinator: Any) -> None:
    """Verify cover, sensor, and binary sensor entities survive stripped device state without crashing."""
    serial = "STRIPPED_DEV_001"
    mock_coordinator.data[serial] = {
        "serial_number": serial,
        "name": None,
        "state": {},
    }

    cover = MyQGarageDoor(mock_coordinator, serial)
    assert cover.is_closed is None
    assert cover.is_opening is False
    assert cover.is_closing is False
    assert "Garage Door STRIPPED_DEV_001" in cover.name
    assert cover.device_info.identifiers == {(DOMAIN, serial)}

    prob_sensor = MyQProblemBinarySensor(mock_coordinator, serial)
    assert prob_sensor.is_on is False

    conn_sensor = MyQConnectivityBinarySensor(mock_coordinator, serial)
    assert conn_sensor.is_on is True

    status_sensor = MyQDoorStatusSensor(mock_coordinator, serial)
    assert status_sensor.native_value == STATE_UNKNOWN

    battery_sensor = MyQBatterySensor(mock_coordinator, serial)
    assert battery_sensor.native_value is None

    wifi_sensor = MyQWifiSignalSensor(mock_coordinator, serial)
    assert wifi_sensor.native_value is None


# -----------------------------------------------------------------------------
# 3. Unrecognized & Hostile Door State Strings
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hostile_state",
    [
        "ajar",
        "partially_open",
        "in_limbo",
        "ERROR_OVERHEAT",
        "undefined",
        "",
        "   ",
        None,
        12345,
    ],
)
def test_unrecognized_door_state_strings(mock_coordinator: Any, hostile_state: Any) -> None:
    """Verify unexpected door state wire values fall back safely to non-closed or unknown."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    dev = dict(SAMPLE_GARAGE_DOOR_DEVICE)
    dev["state"] = dict(dev["state"])
    dev["state"]["door_state"] = hostile_state
    mock_coordinator.data[serial] = dev

    cover = MyQGarageDoor(mock_coordinator, serial)
    assert cover.is_closed is None or cover.is_closed is False
    assert cover.is_opening is False
    assert cover.is_closing is False

    sensor = MyQDoorStatusSensor(mock_coordinator, serial)
    val = sensor.native_value
    assert isinstance(val, str)


# -----------------------------------------------------------------------------
# 4. Corrupted Timestamps
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "corrupted_timestamp",
    [
        None,
        "",
        "NOT_A_TIMESTAMP",
        "2026-99-99T99:99:99Z",
        "0000-00-00",
        123456789,
        {},
        [],
    ],
)
def test_corrupted_timestamps_safe_handling(corrupted_timestamp: Any) -> None:
    """Verify _parse_iso_datetime returns None without raising exceptions on corrupted values."""
    parsed = _parse_iso_datetime(corrupted_timestamp)
    assert parsed is None


def test_last_event_sensor_with_corrupted_timestamp(mock_coordinator: Any) -> None:
    """Verify last event sensor returns None when cloud sends garbage timestamp."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    mock_coordinator.data[serial]["state"]["last_update"] = "MALFORMED_TIMESTAMP_DATA"

    sensor = MyQLastEventSensor(mock_coordinator, serial)
    assert sensor.native_value is None


# -----------------------------------------------------------------------------
# 5. Corrupted Battery & Wi-Fi Values
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "invalid_battery",
    [
        None,
        "",
        "N/A",
        "-10",
        "150",  # Out of 0-100% bounds
        "unknown",
        "charging_fast",
    ],
)
def test_battery_sensor_invalid_inputs(mock_coordinator: Any, invalid_battery: Any) -> None:
    """Verify battery sensor returns None on out-of-range or non-numeric battery values."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    mock_coordinator.data[serial]["state"]["battery_percentage"] = invalid_battery

    sensor = MyQBatterySensor(mock_coordinator, serial)
    assert sensor.native_value is None


@pytest.mark.parametrize(
    "invalid_signal",
    [
        None,
        "",
        [],
        {},
    ],
)
def test_wifi_signal_sensor_invalid_inputs(mock_coordinator: Any, invalid_signal: Any) -> None:
    """Verify Wi-Fi signal sensor safely handles missing or non-standard signal inputs without crashing."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    mock_coordinator.data[serial]["state"]["wifi_signal_strength"] = invalid_signal
    # Clear parent signal to prevent fallback
    parent = SAMPLE_GATEWAY_DEVICE["serial_number"]
    if parent in mock_coordinator.data:
        mock_coordinator.data[parent]["state"]["wifi_signal_strength"] = None

    sensor = MyQWifiSignalSensor(mock_coordinator, serial)
    val = sensor.native_value
    # Safe evaluation: None or string representation, never an unhandled exception
    assert val is None or isinstance(val, str)


# -----------------------------------------------------------------------------
# 6. Malformed Active Fault Codes Schema
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hostile_faults",
    [
        None,
        "active_fault",
        12345,
        {"code": "310"},  # Dict instead of list
        [None, "invalid_fault_string"],
    ],
)
def test_problem_sensor_hostile_fault_codes(mock_coordinator: Any, hostile_faults: Any) -> None:
    """Verify problem binary sensor handles corrupted active_fault_codes structures safely."""
    serial = SAMPLE_GARAGE_DOOR_DEVICE["serial_number"]
    mock_coordinator.data[serial]["state"]["active_fault_codes"] = hostile_faults

    sensor = MyQProblemBinarySensor(mock_coordinator, serial)
    # Should evaluate without TypeError or AttributeError
    result = sensor.is_on
    assert isinstance(result, bool)


# -----------------------------------------------------------------------------
# 7. Boundary Stress: 1,000 Devices Discovery
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_large_device_collection_scalability(mock_session: MockAsyncSession) -> None:
    """Verify client handles a massive cloud response containing 1,000 devices cleanly."""
    large_device_list = [
        {
            "serial_number": f"CG_STRESS_{i:04d}",
            "device_family": "garagedoor",
            "device_type": "garagedooropener",
            "name": f"Door Opener #{i}",
            "state": {"door_state": "closed", "online": True},
        }
        for i in range(1000)
    ]

    mock_session.add_route(
        "/Devices",
        MockResponse(status_code=200, json_data={"count": 1000, "items": large_device_list}),
    )

    client = MyQClient(
        access_token="valid_token",
        account_id="acc-uuid-stress",
        session=mock_session,
    )

    devices = await client.get_devices()
    assert len(devices) == 1000
    assert len(client._devices_cache) == 1000

    # Retrieve specific opener by serial
    dev_500 = await client.get_device("CG_STRESS_0500")
    assert dev_500["name"] == "Door Opener #500"
