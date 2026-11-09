"""Pytest fixtures and Home Assistant mock infrastructure for GarageMyQ tests.

Conforms to Requirement R6 (ZERO live calls to myq-cloud.com, 100% mocked network responses).
Provides high-fidelity mocks for Home Assistant core and entity frameworks when running
in lightweight test environments.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from enum import Enum, IntFlag
import json as json_module
import sys
from pathlib import Path
import types
from typing import Any, Callable, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure repository root is on sys.path
_REPO_ROOT = str(Path(__file__).parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# -----------------------------------------------------------------------------
# 1. High-Fidelity Mock Infrastructure for Home Assistant & Voluptuous
# -----------------------------------------------------------------------------

def _setup_mock_homeassistant() -> None:
    """Inject mock homeassistant and voluptuous packages into sys.modules if not installed."""
    if "homeassistant" in sys.modules:
        return

    # --- Voluptuous mock ---
    if "voluptuous" not in sys.modules:
        vol_mod = types.ModuleType("voluptuous")

        class Schema:
            def __init__(self, schema: Any, extra: Any = None) -> None:
                self.schema = schema
                self.extra = extra

            def __call__(self, data: Any) -> Any:
                if not isinstance(data, dict):
                    return data
                res = dict(data)
                # Check required fields
                if isinstance(self.schema, dict):
                    for k, v in self.schema.items():
                        if isinstance(k, Required) and k.key not in res:
                            if k.default is not Required.UNDEFINED:
                                res[k.key] = k.default
                            else:
                                raise ValueError(f"Missing required field: {k.key}")
                return res

        class Required:
            UNDEFINED = object()
            def __init__(self, key: str, default: Any = UNDEFINED, description: Any = None) -> None:
                self.key = key
                self.default = default
                self.description = description

        class OptionalKey:
            UNDEFINED = object()
            def __init__(self, key: str, default: Any = UNDEFINED, description: Any = None) -> None:
                self.key = key
                self.default = default
                self.description = description

        def All(*validators: Any) -> Callable[[Any], Any]:
            def validator(v: Any) -> Any:
                for val in validators:
                    if callable(val):
                        v = val(v)
                return v
            return validator

        def Coerce(target_type: type) -> Callable[[Any], Any]:
            def validator(v: Any) -> Any:
                return target_type(v)
            return validator

        def Range(min: Optional[int] = None, max: Optional[int] = None) -> Callable[[Any], Any]:
            def validator(v: Any) -> Any:
                if min is not None and v < min:
                    raise ValueError(f"Value {v} < min {min}")
                if max is not None and v > max:
                    raise ValueError(f"Value {v} > max {max}")
                return v
            return validator

        vol_mod.Schema = Schema
        vol_mod.Required = Required
        vol_mod.Optional = OptionalKey
        vol_mod.All = All
        vol_mod.Coerce = Coerce
        vol_mod.Range = Range
        sys.modules["voluptuous"] = vol_mod

    # --- Home Assistant Core ---
    ha_mod = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha_mod

    # const
    const_mod = types.ModuleType("homeassistant.const")
    class Platform(str, Enum):
        COVER = "cover"
        LIGHT = "light"
        BINARY_SENSOR = "binary_sensor"
        SENSOR = "sensor"
        BUTTON = "button"
    const_mod.Platform = Platform
    const_mod.PERCENTAGE = "%"
    const_mod.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"
    sys.modules["homeassistant.const"] = const_mod

    # exceptions
    exc_mod = types.ModuleType("homeassistant.exceptions")
    class HomeAssistantError(Exception):
        pass
    class ConfigEntryAuthFailed(HomeAssistantError):
        pass
    class ConfigEntryNotReady(HomeAssistantError):
        pass
    exc_mod.HomeAssistantError = HomeAssistantError
    exc_mod.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exc_mod.ConfigEntryNotReady = ConfigEntryNotReady
    sys.modules["homeassistant.exceptions"] = exc_mod

    # core
    core_mod = types.ModuleType("homeassistant.core")
    def callback(func: Any) -> Any:
        return func
    core_mod.callback = callback

    class MockHomeAssistant:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}
            self.config_entries = MockConfigEntriesManager(self)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = None

    core_mod.HomeAssistant = MockHomeAssistant
    sys.modules["homeassistant.core"] = core_mod

    # data_entry_flow
    def_mod = types.ModuleType("homeassistant.data_entry_flow")
    def_mod.FlowResult = dict[str, Any]
    sys.modules["homeassistant.data_entry_flow"] = def_mod

    # config_entries
    ce_mod = types.ModuleType("homeassistant.config_entries")
    class MockConfigEntry:
        def __init__(
            self,
            entry_id: str = "test_entry_id",
            domain: str = "myq_apk",
            title: str = "myQ Test Account",
            data: Optional[dict[str, Any]] = None,
            options: Optional[dict[str, Any]] = None,
            unique_id: Optional[str] = "test_account_uuid",
        ) -> None:
            self.entry_id = entry_id
            self.domain = domain
            self.title = title
            self.data = dict(data or {})
            self.options = dict(options or {})
            self.unique_id = unique_id
            self._update_listeners: list[Callable[..., Any]] = []
            self._unload_callbacks: list[Callable[..., Any]] = []

        def add_update_listener(self, listener: Callable[..., Any]) -> Callable[[], None]:
            self._update_listeners.append(listener)
            return lambda: self._update_listeners.remove(listener)

        def async_on_unload(self, callback_func: Callable[..., Any]) -> None:
            self._unload_callbacks.append(callback_func)

    class MockConfigEntriesManager:
        def __init__(self, hass: Any) -> None:
            self.hass = hass
            self._entries: dict[str, MockConfigEntry] = {}

        def async_get_entry(self, entry_id: str) -> Optional[MockConfigEntry]:
            return self._entries.get(entry_id)

        def async_update_entry(
            self, entry: MockConfigEntry, data: Optional[dict[str, Any]] = None, options: Optional[dict[str, Any]] = None
        ) -> None:
            if data is not None:
                entry.data = dict(data)
            if options is not None:
                entry.options = dict(options)
            for listener in list(entry._update_listeners):
                listener(self.hass, entry)

        async def async_forward_entry_setups(self, entry: MockConfigEntry, platforms: list[Any]) -> None:
            pass

        async def async_unload_platforms(self, entry: MockConfigEntry, platforms: list[Any]) -> bool:
            return True

        async def async_reload(self, entry_id: str) -> bool:
            return True

    class ConfigFlow:
        VERSION = 1

        def __init_subclass__(cls, domain: Optional[str] = None, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls._domain = domain

        def __init__(self) -> None:
            self.hass: Any = None
            self.context: dict[str, Any] = {}
            self.unique_id: Optional[str] = None

        def async_show_menu(self, step_id: str, menu_options: list[str]) -> dict[str, Any]:
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options}

        def async_show_form(
            self,
            step_id: str,
            data_schema: Any = None,
            errors: Optional[dict[str, str]] = None,
            description_placeholders: Optional[dict[str, str]] = None,
        ) -> dict[str, Any]:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(self, title: str, data: dict[str, Any]) -> dict[str, Any]:
            return {"type": "create_entry", "title": title, "data": data}

        def async_update_reload_and_abort(self, entry: MockConfigEntry, data: dict[str, Any]) -> dict[str, Any]:
            entry.data = dict(data)
            return {"type": "abort", "reason": "reauth_successful", "data": data}

        async def async_set_unique_id(self, unique_id: str) -> None:
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            pass

    class OptionsFlow:
        def __init__(self, config_entry: MockConfigEntry) -> None:
            self.config_entry = config_entry

        def async_show_form(
            self,
            step_id: str,
            data_schema: Any = None,
            errors: Optional[dict[str, str]] = None,
        ) -> dict[str, Any]:
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}

        def async_create_entry(self, title: str, data: dict[str, Any]) -> dict[str, Any]:
            return {"type": "create_entry", "title": title, "data": data}

    ce_mod.ConfigEntry = MockConfigEntry
    ce_mod.ConfigFlow = ConfigFlow
    ce_mod.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = ce_mod

    # helpers
    helpers_mod = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers_mod

    # helpers.entity
    he_mod = types.ModuleType("homeassistant.helpers.entity")
    class DeviceInfo(dict):
        def __init__(
            self,
            identifiers: Optional[set[tuple[str, str]]] = None,
            name: Optional[str] = None,
            manufacturer: Optional[str] = None,
            model: Optional[str] = None,
            sw_version: Optional[str] = None,
            via_device: Optional[tuple[str, str]] = None,
            **kwargs: Any,
        ) -> None:
            super().__init__()
            if identifiers is not None:
                self["identifiers"] = identifiers
            if name is not None:
                self["name"] = name
            if manufacturer is not None:
                self["manufacturer"] = manufacturer
            if model is not None:
                self["model"] = model
            if sw_version is not None:
                self["sw_version"] = sw_version
            if via_device is not None:
                self["via_device"] = via_device
            self.update(kwargs)

        @property
        def identifiers(self) -> Any:
            return self.get("identifiers")

        @property
        def manufacturer(self) -> Any:
            return self.get("manufacturer")

        @property
        def model(self) -> Any:
            return self.get("model")

        @property
        def sw_version(self) -> Any:
            return self.get("sw_version")

        @property
        def via_device(self) -> Any:
            return self.get("via_device")

    class EntityCategory(str, Enum):
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    class Entity:
        _attr_unique_id: Optional[str] = None
        _attr_name: Optional[str] = None
        _attr_device_class: Any = None
        _attr_icon: Optional[str] = None
        _attr_entity_category: Any = None

        @property
        def unique_id(self) -> Optional[str]:
            return self._attr_unique_id

        @property
        def device_info(self) -> Optional[DeviceInfo]:
            return None

    he_mod.DeviceInfo = DeviceInfo
    he_mod.EntityCategory = EntityCategory
    he_mod.Entity = Entity
    sys.modules["homeassistant.helpers.entity"] = he_mod

    # helpers.entity_platform
    hep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    hep_mod.AddEntitiesCallback = Callable[[list[Any]], None]
    sys.modules["homeassistant.helpers.entity_platform"] = hep_mod

    # helpers.update_coordinator
    huc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")
    class UpdateFailed(HomeAssistantError):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, hass: Any, logger: Any, name: str, update_interval: Any) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: Any = {}
            self.last_update_success = True

        async def async_config_entry_first_refresh(self) -> None:
            self.data = await self._async_update_data()
            self.last_update_success = True

        async def async_request_refresh(self) -> None:
            try:
                self.data = await self._async_update_data()
                self.last_update_success = True
            except Exception:
                self.last_update_success = False
                raise

        async def _async_update_data(self) -> Any:
            raise NotImplementedError

    class CoordinatorEntity(Entity):
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator
            self.hass = coordinator.hass

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

    huc_mod.UpdateFailed = UpdateFailed
    huc_mod.DataUpdateCoordinator = DataUpdateCoordinator
    huc_mod.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = huc_mod

    # components
    comp_mod = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = comp_mod

    # components.cover
    cov_mod = types.ModuleType("homeassistant.components.cover")
    class CoverDeviceClass(str, Enum):
        GARAGE = "garage"
    class CoverEntityFeature(IntFlag):
        OPEN = 1
        CLOSE = 2
        SET_POSITION = 4
        STOP = 8
    class CoverEntity(Entity):
        _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        _attr_device_class = CoverDeviceClass.GARAGE
    cov_mod.CoverDeviceClass = CoverDeviceClass
    cov_mod.CoverEntityFeature = CoverEntityFeature
    cov_mod.CoverEntity = CoverEntity
    sys.modules["homeassistant.components.cover"] = cov_mod

    # components.light
    lit_mod = types.ModuleType("homeassistant.components.light")
    class ColorMode(str, Enum):
        ONOFF = "onoff"
        BRIGHTNESS = "brightness"
    class LightEntity(Entity):
        _attr_color_mode = ColorMode.ONOFF
        _attr_supported_color_modes = {ColorMode.ONOFF}
    lit_mod.ColorMode = ColorMode
    lit_mod.LightEntity = LightEntity
    sys.modules["homeassistant.components.light"] = lit_mod

    # components.binary_sensor
    bs_mod = types.ModuleType("homeassistant.components.binary_sensor")
    class BinarySensorDeviceClass(str, Enum):
        PROBLEM = "problem"
        CONNECTIVITY = "connectivity"
    class BinarySensorEntity(Entity):
        _attr_device_class: Any = None
    bs_mod.BinarySensorDeviceClass = BinarySensorDeviceClass
    bs_mod.BinarySensorEntity = BinarySensorEntity
    sys.modules["homeassistant.components.binary_sensor"] = bs_mod

    # components.sensor
    sen_mod = types.ModuleType("homeassistant.components.sensor")
    class SensorDeviceClass(str, Enum):
        TIMESTAMP = "timestamp"
        SIGNAL_STRENGTH = "signal_strength"
        BATTERY = "battery"
    class SensorStateClass(str, Enum):
        MEASUREMENT = "measurement"
        TOTAL = "total"
    class SensorEntity(Entity):
        _attr_device_class: Any = None
        _attr_state_class: Any = None
        _attr_native_unit_of_measurement: Any = None

        @property
        def native_unit_of_measurement(self) -> Optional[str]:
            return getattr(self, "_attr_native_unit_of_measurement", None)

        @property
        def device_class(self) -> Any:
            return getattr(self, "_attr_device_class", None)

        @property
        def state_class(self) -> Any:
            return getattr(self, "_attr_state_class", None)

    sen_mod.SensorDeviceClass = SensorDeviceClass
    sen_mod.SensorStateClass = SensorStateClass
    sen_mod.SensorEntity = SensorEntity
    sys.modules["homeassistant.components.sensor"] = sen_mod

    # components.button
    btn_mod = types.ModuleType("homeassistant.components.button")
    class ButtonDeviceClass(str, Enum):
        UPDATE = "update"
        RESTART = "restart"
    class ButtonEntity(Entity):
        _attr_device_class: Any = None

        @property
        def device_class(self) -> Any:
            return getattr(self, "_attr_device_class", None)
    btn_mod.ButtonDeviceClass = ButtonDeviceClass
    btn_mod.ButtonEntity = ButtonEntity
    sys.modules["homeassistant.components.button"] = btn_mod

    # components.diagnostics
    diag_mod = types.ModuleType("homeassistant.components.diagnostics")
    def async_redact_data(data: Any, to_redact: Any) -> Any:
        lower_redact = {str(k).lower() for k in to_redact}
        if isinstance(data, dict):
            res = {}
            for k, v in data.items():
                if str(k).lower() in lower_redact:
                    res[k] = "**REDACTED**"
                elif isinstance(v, (dict, list)):
                    res[k] = async_redact_data(v, to_redact)
                else:
                    res[k] = v
            return res
        if isinstance(data, list):
            return [async_redact_data(x, to_redact) for x in data]
        return data

    diag_mod.async_redact_data = async_redact_data
    sys.modules["homeassistant.components.diagnostics"] = diag_mod

    # util.dt
    dt_mod = types.ModuleType("homeassistant.util.dt")
    def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        if not dt_str:
            return None
        try:
            clean = dt_str.replace("Z", "+00:00")
            return datetime.fromisoformat(clean)
        except Exception:
            return None
    dt_mod.parse_datetime = parse_datetime
    sys.modules["homeassistant.util"] = types.ModuleType("homeassistant.util")
    sys.modules["homeassistant.util.dt"] = dt_mod


# Initialize mock Home Assistant environment prior to test module imports
_setup_mock_homeassistant()


# -----------------------------------------------------------------------------
# 2. Mock HTTP Network Engine (AsyncSession & Response)
# -----------------------------------------------------------------------------

class MockResponse:
    """Mock HTTP response matching curl_cffi and aiohttp semantics."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.status = status_code
        self._json_data = json_data
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json_module.dumps(json_data)
        else:
            self.text = ""
        self.headers = headers or {}
        self.content = self.text.encode("utf-8")

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        if self.text:
            return json_module.loads(self.text)
        return None


class MockAsyncSession:
    """Mock curl_cffi AsyncSession recording all outbound requests without live network access."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._route_handlers: list[tuple[Callable[[str, str], bool], Any]] = []
        self._default_status = 200
        self._closed = False

    def add_route(
        self,
        matcher: Callable[[str, str], bool] | str,
        response_or_handler: Any,
    ) -> None:
        """Register custom route handler. Matcher can be a string substring or callable."""
        if isinstance(matcher, str):
            prefix = matcher
            matcher_func = lambda m, u: prefix in u
        else:
            matcher_func = matcher
        self._route_handlers.insert(0, (matcher_func, response_or_handler))

    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        timeout: float = 20.0,
        allow_redirects: bool = True,
        **kwargs: Any,
    ) -> MockResponse:
        """Record request and return mock response."""
        record = {
            "method": method.upper(),
            "url": url,
            "headers": dict(headers or {}),
            "params": dict(params or {}),
            "data": data,
            "json": json,
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        }
        self.requests.append(record)

        # Check custom route handlers
        for matcher_func, handler in self._route_handlers:
            if matcher_func(method.upper(), url):
                if callable(handler):
                    res = handler(record)
                    if asyncio.iscoroutine(res):
                        return await res
                    return res
                if hasattr(handler, "status_code") and (hasattr(handler, "json") or hasattr(handler, "text")):
                    return handler
                if isinstance(handler, tuple) and len(handler) >= 2:
                    return MockResponse(status_code=handler[0], json_data=handler[1])
                return MockResponse(status_code=200, json_data=handler)

        # Default fallbacks based on URL patterns
        if "/connect/token" in url:
            return MockResponse(
                status_code=200,
                json_data={
                    "access_token": "mock_new_access_token",
                    "refresh_token": "mock_new_rolling_refresh_token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "MyQ_Residential offline_access",
                },
            )

        if "/api/v6.0/accounts" in url:
            return MockResponse(
                status_code=200,
                json_data={
                    "accounts": [
                        {
                            "id": "e36e6761-0000-4000-8000-0123456789ab",
                            "name": "Home Garage",
                            "created_by": "u-test-user-1234",
                        }
                    ]
                },
            )

        if "/Devices" in url:
            return MockResponse(
                status_code=200,
                json_data={
                    "count": 2,
                    "items": copy.deepcopy([
                        SAMPLE_GATEWAY_DEVICE,
                        SAMPLE_GARAGE_DOOR_DEVICE,
                    ]),
                },
            )

        if "/open" in url or "/close" in url or "/lamps/" in url:
            return MockResponse(status_code=204, text="")

        return MockResponse(status_code=200, json_data={})

    async def get(self, url: str, **kwargs: Any) -> MockResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> MockResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> MockResponse:
        return await self.request("PUT", url, **kwargs)

    async def close(self) -> None:
        self._closed = True


# -----------------------------------------------------------------------------
# 3. Canonical Device Payloads (PROTOCOL.md §6 & §7)
# -----------------------------------------------------------------------------

SAMPLE_GATEWAY_DEVICE = {
    "serial_number": "GW010023ABCD",
    "device_family": "gateway",
    "device_type": "hub",
    "device_model": "MYQ-G0401",
    "device_platform": "Android",
    "name": "Smart Garage Hub",
    "created_date": "2024-01-15T12:00:00.000Z",
    "state": {
        "online": True,
        "wifi_signal_strength": "excellent",
        "firmware_version": "1.32",
        "last_update": "2026-11-08T22:30:00.000Z",
        "last_is_online_change": "2026-11-08T20:00:00.000Z",
    },
    "specifications": {},
    "capabilities": {},
}

SAMPLE_GARAGE_DOOR_DEVICE = {
    "serial_number": "CG0850123456",
    "device_family": "garagedoor",
    "device_type": "virtualgaragedooropener",
    "device_model": "Chamberlain Opener",
    "device_platform": None,
    "name": "Main Garage Door",
    "created_date": "2024-01-15T12:05:00.000Z",
    "state": {
        "parent_serial_number": "GW010023ABCD",
        "door_state": "closed",
        "door_sensor_state": "closed",
        "online": True,
        "wifi_signal_strength": "-65",
        "battery_percentage": "95",
        "battery_voltage": "12.6",
        "battery_backup_state": "Charging",
        "dps_low_battery_mode": False,
        "low_battery": False,
        "sensor_comm_error": False,
        "attached_work_light_error_present": False,
        "is_unattended_open_allowed": True,
        "is_unattended_close_allowed": True,
        "firmware_version": "2.45",
        "last_update": "2026-11-08T22:45:00.000Z",
        "last_is_online_change": "2026-11-08T20:10:00.000Z",
        "active_fault_codes": [],
    },
    "specifications": {},
    "capabilities": {
        "work_light": True,
    },
}

SAMPLE_LAMP_DEVICE = {
    "serial_number": "LM0987654321",
    "device_family": "lamp",
    "device_type": "lampmodule",
    "device_model": "Remote Lamp Switch",
    "name": "Garage Overhead Light",
    "state": {
        "parent_serial_number": "GW010023ABCD",
        "lamp_state": "On",
        "online": True,
        "firmware_version": "1.10",
        "last_update": "2026-11-08T22:40:00.000Z",
    },
    "capabilities": {},
}


# -----------------------------------------------------------------------------
# 4. Pytest Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def mock_session() -> MockAsyncSession:
    """Fixture providing a fresh MockAsyncSession recording outbound HTTP traffic."""
    return MockAsyncSession()


@pytest.fixture
def sample_devices_payload() -> list[dict[str, Any]]:
    """Fixture returning sample gateway, garage door opener, and lamp devices."""
    return copy.deepcopy([
        SAMPLE_GATEWAY_DEVICE,
        SAMPLE_GARAGE_DOOR_DEVICE,
        SAMPLE_LAMP_DEVICE,
    ])


@pytest.fixture
def mock_hass() -> Any:
    """Fixture returning a mock HomeAssistant instance."""
    from homeassistant.core import HomeAssistant
    return HomeAssistant()


@pytest.fixture
def mock_config_entry() -> Any:
    """Fixture returning a mock ConfigEntry."""
    from homeassistant.config_entries import ConfigEntry
    return ConfigEntry(
        entry_id="test_entry_123",
        domain="myq_apk",
        title="myQ (Home Garage)",
        data={
            "access_token": "mock_initial_access_token",
            "refresh_token": "mock_initial_refresh_token",
            "expires_at": 1800000000.0,
            "account_id": "e36e6761-0000-4000-8000-0123456789ab",
            "auth_mode": "automated",
            "username": "user@example.com",
            "scan_interval": 180,
        },
        options={"scan_interval": 180},
        unique_id="e36e6761-0000-4000-8000-0123456789ab",
    )


@pytest.fixture
def mock_client(mock_session: MockAsyncSession) -> Any:
    """Fixture returning a MyQClient wired to the mock HTTP session."""
    from custom_components.myq_apk.api import MyQClient
    client = MyQClient(
        access_token="mock_initial_access_token",
        refresh_token="mock_initial_refresh_token",
        expires_at=1800000000.0,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )
    return client


@pytest.fixture
async def mock_coordinator(
    mock_hass: Any,
    mock_client: Any,
    mock_config_entry: Any,
) -> Any:
    """Fixture returning an initialized MyQDataUpdateCoordinator with sample device data."""
    from custom_components.myq_apk.coordinator import MyQDataUpdateCoordinator
    coordinator = MyQDataUpdateCoordinator(
        mock_hass,
        client=mock_client,
        entry=mock_config_entry,
    )
    await coordinator.async_config_entry_first_refresh()
    return coordinator
