"""Sensor entity platform for Chamberlain/LiftMaster myQ integration.

Provides:
- Door Status sensor (open, closed, opening, closing, stopped, autoreverse)
- Last Event timestamp sensor
- Wi-Fi Signal strength sensor
- Backup Battery sensor
- Session Health diagnostic sensor
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import MyQClient
from .const import CONF_AUTH_MODE, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import MyQDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _parse_iso_datetime(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 UTC timestamp string to aware datetime."""
    if not date_str:
        return None
    try:
        # Try homeassistant dt_util first if available
        parsed = dt_util.parse_datetime(date_str)
        if parsed is not None:
            return parsed
    except Exception:
        pass

    try:
        clean_str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up myQ sensor entities from config entry."""
    coordinator: MyQDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for serial_number, device in coordinator.data.items():
        if MyQClient.is_garage_door(device):
            entities.append(MyQDoorStatusSensor(coordinator, serial_number))
            entities.append(MyQLastEventSensor(coordinator, serial_number))
            entities.append(MyQWifiSignalSensor(coordinator, serial_number))
            entities.append(MyQBatterySensor(coordinator, serial_number))
            entities.append(MyQSessionHealthSensor(coordinator, serial_number, entry))
        elif MyQClient.is_gateway(device):
            entities.append(MyQWifiSignalSensor(coordinator, serial_number))
            entities.append(MyQLastEventSensor(coordinator, serial_number))

    async_add_entities(entities)


class MyQDoorStatusSensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], SensorEntity
):
    """Sensor reporting the physical status string of the garage door."""

    _attr_icon = "mdi:garage"

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize door status sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_door_status"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name."""
        device = self.device_data
        base_name = device.get("name") or f"Garage Door {self._serial_number}"
        return f"{base_name} Door Status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo."""
        device = self.device_data
        parent_serial = MyQClient.get_parent_serial_number(device)
        state = device.get("state", {})

        info = DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device.get("name") or f"Garage Door {self._serial_number}",
            manufacturer="Chamberlain",
            model=device.get("device_model") or device.get("device_type") or "Garage Door Opener",
            sw_version=state.get("firmware_version"),
        )
        if parent_serial and parent_serial != self._serial_number:
            info["via_device"] = (DOMAIN, parent_serial)
        return info

    @property
    def native_value(self) -> str:
        """Return raw door state string."""
        return MyQClient.get_door_state(self.device_data)


class MyQLastEventSensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], SensorEntity
):
    """Sensor reporting the ISO timestamp of the last door or device state event."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize last event sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_last_event"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name."""
        device = self.device_data
        base_name = device.get("name") or f"Device {self._serial_number}"
        return f"{base_name} Last Event"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo."""
        device = self.device_data
        parent_serial = MyQClient.get_parent_serial_number(device)
        state = device.get("state", {})

        info = DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device.get("name") or f"Device {self._serial_number}",
            manufacturer="Chamberlain",
            model=device.get("device_model") or device.get("device_type") or "myQ Device",
            sw_version=state.get("firmware_version"),
        )
        if parent_serial and parent_serial != self._serial_number:
            info["via_device"] = (DOMAIN, parent_serial)
        return info

    @property
    def native_value(self) -> Optional[datetime]:
        """Return parsed datetime of last update event."""
        state = self.device_data.get("state", {})
        raw_timestamp = state.get("last_update") or state.get("updated_date")
        return _parse_iso_datetime(raw_timestamp)


class MyQWifiSignalSensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], SensorEntity
):
    """Sensor reporting Wi-Fi signal strength or RSSI."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize Wi-Fi signal sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_wifi_signal"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name."""
        device = self.device_data
        base_name = device.get("name") or f"Device {self._serial_number}"
        return f"{base_name} Wi-Fi Signal"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo."""
        device = self.device_data
        parent_serial = MyQClient.get_parent_serial_number(device)
        state = device.get("state", {})

        info = DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device.get("name") or f"Device {self._serial_number}",
            manufacturer="Chamberlain",
            model=device.get("device_model") or device.get("device_type") or "myQ Device",
            sw_version=state.get("firmware_version"),
        )
        if parent_serial and parent_serial != self._serial_number:
            info["via_device"] = (DOMAIN, parent_serial)
        return info

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        """Return dBm if numeric signal strength is reported."""
        raw = self._get_raw_signal()
        if raw is not None:
            try:
                int(raw)
                return SIGNAL_STRENGTH_DECIBELS_MILLIWATT
            except ValueError:
                pass
        return None

    def _get_raw_signal(self) -> Optional[str]:
        """Retrieve signal strength from device or parent gateway."""
        state = self.device_data.get("state", {})
        sig = state.get("wifi_signal_strength")
        if sig is not None:
            return str(sig)

        parent_serial = MyQClient.get_parent_serial_number(self.device_data)
        if parent_serial and parent_serial in self.coordinator.data:
            parent_state = self.coordinator.data[parent_serial].get("state", {})
            p_sig = parent_state.get("wifi_signal_strength")
            if p_sig is not None:
                return str(p_sig)
        return None

    @property
    def native_value(self) -> Optional[str | int]:
        """Return signal strength as integer dBm or string rating."""
        raw = self._get_raw_signal()
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return str(raw).capitalize()


class MyQBatterySensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], SensorEntity
):
    """Sensor reporting battery backup percentage and state."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize battery sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_battery"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name."""
        device = self.device_data
        base_name = device.get("name") or f"Garage Door {self._serial_number}"
        return f"{base_name} Battery"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo."""
        device = self.device_data
        parent_serial = MyQClient.get_parent_serial_number(device)
        state = device.get("state", {})

        info = DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device.get("name") or f"Garage Door {self._serial_number}",
            manufacturer="Chamberlain",
            model=device.get("device_model") or device.get("device_type") or "Garage Door Opener",
            sw_version=state.get("firmware_version"),
        )
        if parent_serial and parent_serial != self._serial_number:
            info["via_device"] = (DOMAIN, parent_serial)
        return info

    @property
    def native_value(self) -> Optional[int]:
        """Return battery percentage if present."""
        state = self.device_data.get("state", {})
        raw_pct = state.get("battery_percentage")
        if raw_pct is not None:
            try:
                val = int(raw_pct)
                if 0 <= val <= 100:
                    return val
            except (ValueError, TypeError):
                pass
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return battery voltage and operational backup state."""
        state = self.device_data.get("state", {})
        return {
            "battery_voltage": state.get("battery_voltage"),
            "battery_backup_state": state.get("battery_backup_state"),
            "dps_low_battery_mode": state.get("dps_low_battery_mode"),
            "low_battery": state.get("low_battery"),
        }


class MyQSessionHealthSensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], SensorEntity
):
    """Diagnostic sensor reporting OAuth session health, token validity, and timing."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-check"

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
        entry: ConfigEntry,
    ) -> None:
        """Initialize session health sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._entry = entry
        self._attr_unique_id = f"{serial_number}_session_health"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name."""
        device = self.device_data
        base_name = device.get("name") or f"Garage Door {self._serial_number}"
        return f"{base_name} Session Health"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo."""
        device = self.device_data
        parent_serial = MyQClient.get_parent_serial_number(device)
        state = device.get("state", {})

        info = DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device.get("name") or f"Garage Door {self._serial_number}",
            manufacturer="Chamberlain",
            model=device.get("device_model") or device.get("device_type") or "Garage Door Opener",
            sw_version=state.get("firmware_version"),
        )
        if parent_serial and parent_serial != self._serial_number:
            info["via_device"] = (DOMAIN, parent_serial)
        return info

    @property
    def native_value(self) -> str:
        """Return operational health status of client OAuth session."""
        client = self.coordinator.client
        if not client.access_token:
            return "no_token"
        if client.is_token_expiring(leeway=0):
            return "expired"
        if client.is_token_expiring(leeway=300):
            return "expiring_soon"
        if not self.coordinator.last_update_success:
            return "cloud_disconnected"
        return "healthy"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return session diagnostics."""
        client = self.coordinator.client
        expires_at_dt = None
        if client.expires_at is not None:
            expires_at_dt = datetime.fromtimestamp(client.expires_at, tz=timezone.utc).isoformat()

        return {
            "expires_at": expires_at_dt,
            "has_refresh_token": bool(client.refresh_token),
            "auth_mode": self._entry.data.get(CONF_AUTH_MODE),
            "scan_interval": self._entry.options.get(
                CONF_SCAN_INTERVAL,
                self._entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ),
            "stalled": self.coordinator.is_device_stalled(self._serial_number),
        }
