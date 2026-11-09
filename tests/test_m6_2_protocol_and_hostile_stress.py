"""Adversarial stress and protocol compliance test suite (Milestone M6-2).

Empirically tests:
1. Malformed, corrupted, truncated, and deeply nested hostile JSON payloads.
2. Unexpected door states, missing telemetry dictionaries, and non-numeric fields.
3. Protocol compliance: empty PUT payloads (data=b"") with exact headers matching PROTOCOL.md.
4. Zero live network calls to myq-cloud.com (with active socket tripwires).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json as json_module
import socket
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.myq_apk.api import MyQClient
from custom_components.myq_apk.binary_sensor import (
    MyQConnectivityBinarySensor,
    MyQProblemBinarySensor,
)
from custom_components.myq_apk.const import (
    API_VERSION,
    APP_VERSION,
    BRAND_ID,
    CLIENT_ID,
    COMMAND_TIMEOUT,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CULTURE,
    DOMAIN,
    HEADER_ANTI_CSRF,
    HEADER_API_VERSION,
    HEADER_APP_VERSION,
    HEADER_APPLICATION_ID,
    HEADER_BRAND_ID,
    HEADER_CULTURE,
    STATE_UNKNOWN,
    USER_AGENT_API,
    MyQAuthError,
    MyQError,
    MyQOfflineError,
    MyQTimeoutError,
)
from custom_components.myq_apk.button import MyQRefreshButton
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
from homeassistant.helpers.update_coordinator import UpdateFailed
from tests.conftest import (
    MockAsyncSession,
    MockResponse,
    SAMPLE_GARAGE_DOOR_DEVICE,
    SAMPLE_GATEWAY_DEVICE,
    SAMPLE_LAMP_DEVICE,
)


# =============================================================================
# PART 1: MALFORMED, CORRUPTED, TRUNCATED & DEEPLY NESTED PAYLOAD FUZZING
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupted_raw_text",
    [
        '{"items": [{"serial_number": "CG123456", "name": "Garage Door", "state": {"door_st',  # Truncated JSON
        '{ "key": undefined, "list": [1, 2, }',  # Malformed JSON syntax
        "<!DOCTYPE html><html><head><title>500 Internal Server Error</title></head></html>",  # HTML 500 error
        "Service Unavailable (503)",  # Plain text error
        "",  # Empty string
        "   ",  # Whitespace
        "null",  # JSON null
        "123456789",  # Root integer
        '"just a string"',  # Root string
        "true",  # Root boolean
        "[1, 2, 3]",  # Root list of ints
    ],
)
async def test_fuzz_corrupted_and_truncated_raw_json(
    mock_session: MockAsyncSession, corrupted_raw_text: str
) -> None:
    """Verify get_devices handles truncated, malformed, or non-object raw responses without unhandled crash."""
    mock_session.add_route("/Devices", MockResponse(status_code=200, text=corrupted_raw_text))

    client = MyQClient(
        access_token="valid_token",
        account_id="acc-uuid-1234",
        session=mock_session,
    )

    devices = await client.get_devices()
    # Must safely evaluate to empty list or parsed items, never raise JSONDecodeError or AttributeError
    assert isinstance(devices, list)


@pytest.mark.asyncio
async def test_fuzz_deeply_nested_hostile_json(mock_coordinator: Any) -> None:
    """Verify coordinator handles deeply nested hostile JSON structures (60 levels) without recursion crash."""
    # Build a 60-level nested dictionary structure
    current: dict[str, Any] = {
        "serial_number": "CG_DEEP_NEST_001",
        "device_family": "garagedoor",
        "device_type": "garagedooropener",
        "name": "Deeply Nested Opener",
        "state": {"door_state": "open", "online": True},
    }
    for level in range(60):
        current = {"level": level, "child": current}

    hostile_device = {
        "serial_number": "CG_DEEP_NEST_001",
        "device_family": "garagedoor",
        "device_type": "garagedooropener",
        "name": "Deeply Nested Opener",
        "state": {
            "door_state": "open",
            "online": True,
            "nested_telemetry": current,
        },
    }

    mock_coordinator.client.get_devices = AsyncMock(return_value=[hostile_device])
    await mock_coordinator.async_request_refresh()

    assert "CG_DEEP_NEST_001" in mock_coordinator.data
    cover = MyQGarageDoor(mock_coordinator, "CG_DEEP_NEST_001")
    assert cover.is_closed is False
    assert cover.is_opening is False
    assert cover.is_closing is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostile_items",
    [
        None,
        12345,
        "not_a_list",
        True,
        {"key": "value"},
        [None, 123, "not_a_device", False, [], {}],
        [{"name": "Missing Serial", "state": {"door_state": "open"}}],
        [{"serial_number": None, "state": {}}],
        [{"serial_number": "", "state": {}}],
    ],
)
async def test_fuzz_hostile_items_collection_structures(
    mock_session: MockAsyncSession, hostile_items: Any
) -> None:
    """Verify get_devices handles hostile items collections gracefully."""
    mock_session.add_route(
        "/Devices",
        MockResponse(status_code=200, json_data={"count": 0, "items": hostile_items}),
    )

    client = MyQClient(
        access_token="valid_token",
        account_id="acc-uuid-1234",
        session=mock_session,
    )

    devices = await client.get_devices()
    assert isinstance(devices, (list, dict))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupted_state",
    [
        None,
        "offline",
        12345,
        True,
        [],
        [{"door_state": "open"}],
        {"door_state": {"nested": "dict"}},
        {"door_state": [1, 2, 3]},
        {"door_state": None},
        {"door_state": 42},
        {"door_state": True},
        {"door_state": ""},
        {"door_state": "   "},
        {"door_state": "exploding_door_state"},
        {"online": "not_a_bool"},
        {"online": None},
        {"wifi_signal_strength": {"rssi": -70}},
        {"battery_percentage": [95]},
        {"active_fault_codes": "not_a_list"},
        {"active_fault_codes": [None, 123, "corrupted_fault", {}]},
        {"parent_serial_number": {"parent": "GW123"}},
    ],
)
async def test_fuzz_missing_and_corrupted_telemetry_dictionaries(
    mock_coordinator: Any, corrupted_state: Any
) -> None:
    """Verify all entity platforms survive corrupted, missing, or non-standard device state objects."""
    serial = "CG_HOSTILE_TELEMETRY"
    hostile_device = {
        "serial_number": serial,
        "device_family": "garagedoor",
        "device_type": "garagedooropener",
        "device_model": "Adversarial Opener",
        "name": "Hostile Opener",
        "state": corrupted_state,
        "capabilities": {"work_light": True},
    }
    mock_coordinator.data[serial] = hostile_device

    # 1. Cover entity
    cover = MyQGarageDoor(mock_coordinator, serial)
    _ = cover.is_closed
    _ = cover.is_opening
    _ = cover.is_closing
    _ = cover.extra_state_attributes
    _ = cover.device_info

    # 2. Problem binary sensor
    prob = MyQProblemBinarySensor(mock_coordinator, serial)
    assert isinstance(prob.is_on, bool)
    _ = prob.extra_state_attributes

    # 3. Connectivity binary sensor
    conn = MyQConnectivityBinarySensor(mock_coordinator, serial)
    assert isinstance(conn.is_on, bool)
    _ = conn.extra_state_attributes

    # 4. Door status sensor
    status = MyQDoorStatusSensor(mock_coordinator, serial)
    assert isinstance(status.native_value, str)

    # 5. Last event sensor
    last_event = MyQLastEventSensor(mock_coordinator, serial)
    assert last_event.native_value is None or isinstance(last_event.native_value, datetime)

    # 6. Wi-Fi signal sensor
    wifi = MyQWifiSignalSensor(mock_coordinator, serial)
    _ = wifi.native_value
    _ = wifi.native_unit_of_measurement

    # 7. Battery sensor
    batt = MyQBatterySensor(mock_coordinator, serial)
    assert batt.native_value is None or (0 <= batt.native_value <= 100)
    _ = batt.extra_state_attributes

    # 8. Session health sensor
    health = MyQSessionHealthSensor(mock_coordinator, serial, mock_coordinator.entry)
    assert isinstance(health.native_value, str)
    _ = health.extra_state_attributes

    # 9. Light entity
    light = MyQWorkLight(mock_coordinator, serial)
    assert isinstance(light.is_on, bool)
    _ = light.device_info

    # 10. Button entity
    button = MyQRefreshButton(mock_coordinator, serial)
    _ = button.device_info

    # 11. Diagnostics
    diag = await async_get_config_entry_diagnostics(mock_coordinator.hass, mock_coordinator.entry)
    assert isinstance(diag, dict)


@pytest.mark.parametrize(
    "extreme_battery",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
        "-Infinity",
        "1e10",
        "-100",
        "101",
        2**64,
        -(2**64),
        None,
        "",
        "   ",
        {},
        [],
        True,
        False,
        "ninety-five",
    ],
)
def test_fuzz_extreme_battery_percentage_values(mock_coordinator: Any, extreme_battery: Any) -> None:
    """Verify battery sensor strictly clamps or discards out-of-range, non-numeric, or NaN values."""
    serial = "CG_BATT_STRESS"
    mock_coordinator.data[serial] = {
        "serial_number": serial,
        "name": "Battery Stress Opener",
        "state": {"battery_percentage": extreme_battery},
    }

    sensor = MyQBatterySensor(mock_coordinator, serial)
    val = sensor.native_value
    # Value MUST be either None or an int strictly between 0 and 100 inclusive
    assert val is None or (isinstance(val, int) and 0 <= val <= 100)


@pytest.mark.parametrize(
    "extreme_wifi",
    [
        float("nan"),
        "nan",
        10**12,
        -999999999,
        {"rssi": -65},
        [1, 2, 3],
        True,
        False,
        None,
        "",
        "   ",
        "\x00",
        "excellent",
        "-68",
    ],
)
def test_fuzz_extreme_wifi_signal_values(mock_coordinator: Any, extreme_wifi: Any) -> None:
    """Verify Wi-Fi signal sensor safely handles extreme numbers, non-strings, and booleans."""
    serial = "CG_WIFI_STRESS"
    mock_coordinator.data[serial] = {
        "serial_number": serial,
        "name": "WiFi Stress Opener",
        "state": {"wifi_signal_strength": extreme_wifi},
    }

    sensor = MyQWifiSignalSensor(mock_coordinator, serial)
    val = sensor.native_value
    unit = sensor.native_unit_of_measurement
    if isinstance(val, int):
        assert unit == "dBm"
    else:
        assert unit is None


# =============================================================================
# PART 2: PROTOCOL COMPLIANCE (EMPTY PUT BODIES, EXACT MOBILE HEADERS)
# =============================================================================

@pytest.mark.asyncio
async def test_protocol_compliance_open_door_empty_put_body(mock_session: MockAsyncSession) -> None:
    """Verify open_door sends empty PUT body (data=b'') with exact mobile headers."""
    client = MyQClient(
        access_token="mock_access_token_12345",
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    await client.open_door("CG0850123456")

    req = next(r for r in mock_session.requests if "/open" in r["url"])

    # 1. HTTP Method must be strictly PUT
    assert req["method"] == "PUT"

    # 2. Path compliance according to PROTOCOL.md §8.1
    expected_path = "/api/v6.0/accounts/e36e6761-0000-4000-8000-0123456789ab/door_openers/CG0850123456/open"
    assert expected_path in req["url"]

    # 3. Body MUST be empty bytes (0 bytes, data=b"") and json is None
    assert req["data"] == b""
    assert req["json"] is None

    # 4. Content-Type header
    assert req["headers"]["Content-Type"] == "application/json; charset=UTF-8"

    # 5. Mobile client fingerprint headers according to PROTOCOL.md §4.1
    headers = req["headers"]
    assert headers["User-Agent"] == USER_AGENT_API
    assert headers[HEADER_BRAND_ID] == BRAND_ID
    assert headers[HEADER_APP_VERSION] == APP_VERSION
    assert headers[HEADER_API_VERSION] == API_VERSION
    assert headers[HEADER_CULTURE] == CULTURE
    assert headers[HEADER_APPLICATION_ID] == CLIENT_ID
    assert headers[HEADER_ANTI_CSRF] == "AntiCSRF"
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer mock_access_token_12345"


@pytest.mark.asyncio
async def test_protocol_compliance_close_door_empty_put_body(mock_session: MockAsyncSession) -> None:
    """Verify close_door sends empty PUT body (data=b'') with exact mobile headers."""
    client = MyQClient(
        access_token="mock_access_token_12345",
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    await client.close_door("CG0850123456")

    req = next(r for r in mock_session.requests if "/close" in r["url"])

    assert req["method"] == "PUT"
    expected_path = "/api/v6.0/accounts/e36e6761-0000-4000-8000-0123456789ab/door_openers/CG0850123456/close"
    assert expected_path in req["url"]
    assert req["data"] == b""
    assert req["json"] is None
    assert req["headers"]["Content-Type"] == "application/json; charset=UTF-8"

    headers = req["headers"]
    assert headers["User-Agent"] == USER_AGENT_API
    assert headers[HEADER_BRAND_ID] == BRAND_ID
    assert headers[HEADER_APP_VERSION] == APP_VERSION
    assert headers[HEADER_API_VERSION] == API_VERSION
    assert headers[HEADER_CULTURE] == CULTURE
    assert headers[HEADER_APPLICATION_ID] == CLIENT_ID
    assert headers[HEADER_ANTI_CSRF] == "AntiCSRF"
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer mock_access_token_12345"


@pytest.mark.asyncio
async def test_protocol_compliance_light_on_empty_put_body(mock_session: MockAsyncSession) -> None:
    """Verify set_light_state(True) sends empty PUT body (data=b'') with exact headers."""
    client = MyQClient(
        access_token="mock_access_token_12345",
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    await client.set_light_state("LM0987654321", True)

    req = next(r for r in mock_session.requests if "/lamps/LM0987654321/on" in r["url"])

    assert req["method"] == "PUT"
    expected_path = "/api/v6.0/accounts/e36e6761-0000-4000-8000-0123456789ab/lamps/LM0987654321/on"
    assert expected_path in req["url"]
    assert req["data"] == b""
    assert req["json"] is None
    assert req["headers"]["Content-Type"] == "application/json; charset=UTF-8"

    headers = req["headers"]
    assert headers["User-Agent"] == USER_AGENT_API
    assert headers[HEADER_BRAND_ID] == BRAND_ID
    assert headers[HEADER_APP_VERSION] == APP_VERSION
    assert headers[HEADER_API_VERSION] == API_VERSION
    assert headers[HEADER_CULTURE] == CULTURE
    assert headers[HEADER_APPLICATION_ID] == CLIENT_ID
    assert headers[HEADER_ANTI_CSRF] == "AntiCSRF"
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer mock_access_token_12345"


@pytest.mark.asyncio
async def test_protocol_compliance_light_off_empty_put_body(mock_session: MockAsyncSession) -> None:
    """Verify set_light_state(False) sends empty PUT body (data=b'') with exact headers."""
    client = MyQClient(
        access_token="mock_access_token_12345",
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    await client.set_light_state("LM0987654321", False)

    req = next(r for r in mock_session.requests if "/lamps/LM0987654321/off" in r["url"])

    assert req["method"] == "PUT"
    expected_path = "/api/v6.0/accounts/e36e6761-0000-4000-8000-0123456789ab/lamps/LM0987654321/off"
    assert expected_path in req["url"]
    assert req["data"] == b""
    assert req["json"] is None
    assert req["headers"]["Content-Type"] == "application/json; charset=UTF-8"

    headers = req["headers"]
    assert headers["User-Agent"] == USER_AGENT_API
    assert headers[HEADER_BRAND_ID] == BRAND_ID
    assert headers[HEADER_APP_VERSION] == APP_VERSION
    assert headers[HEADER_API_VERSION] == API_VERSION
    assert headers[HEADER_CULTURE] == CULTURE
    assert headers[HEADER_APPLICATION_ID] == CLIENT_ID
    assert headers[HEADER_ANTI_CSRF] == "AntiCSRF"
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer mock_access_token_12345"


# =============================================================================
# PART 3: ZERO LIVE NETWORK TRAFFIC TRIPWIRE ENFORCEMENT
# =============================================================================

@pytest.mark.asyncio
async def test_zero_live_network_traffic_enforcement(mock_session: MockAsyncSession) -> None:
    """Empirically verify that 0 live socket calls reach myq-cloud.com or the internet."""
    live_call_attempts: list[tuple[str, int]] = []

    original_socket_connect = socket.socket.connect

    def forbidden_socket_connect(self: socket.socket, address: Any) -> None:
        host, port = address[0], address[1]
        live_call_attempts.append((str(host), int(port)))
        raise RuntimeError(
            f"VIOLATION OF REQUIREMENT R3.6 & R6: Outbound live socket connection attempted to {host}:{port}"
        )

    with patch.object(socket.socket, "connect", forbidden_socket_connect):
        client = MyQClient(
            access_token="mock_token",
            refresh_token="mock_refresh_token",
            account_id="e36e6761-0000-4000-8000-0123456789ab",
            session=mock_session,
        )

        # Execute full battery of client interactions
        await client.get_accounts()
        await client.get_devices()
        await client.open_door("CG0850123456")
        await client.close_door("CG0850123456")
        await client.set_light_state("LM0987654321", True)
        await client.set_light_state("LM0987654321", False)
        await client.refresh_tokens()

        # Invariant: ZERO live socket connections were attempted
        assert len(live_call_attempts) == 0

        # Verify tripwire itself is active: an attempt to call forbidden_socket_connect raises RuntimeError
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(RuntimeError) as exc_info:
            test_sock.connect(("api.myq-cloud.com", 443))
        assert "VIOLATION OF REQUIREMENT R3.6" in str(exc_info.value)
        assert len(live_call_attempts) == 1
        assert live_call_attempts[0][0] == "api.myq-cloud.com"
