"""Binary sensor platform for Chamberlain/LiftMaster myQ integration.

Provides:
- Problem / Obstruction binary sensor (PROBLEM device class) detecting autoreverse,
  stopped mid-travel, 45s stall timeout, safety beam obstruction, sensor communication
  errors, and tilt sensor low battery.
- Cloud Connectivity binary sensor (CONNECTIVITY device class) reporting online
  gateway and cloud reachability status.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MyQClient
from .const import DOMAIN, STATE_AUTOREVERSE, STATE_STOPPED
from .coordinator import MyQDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up myQ binary sensor entities from config entry."""
    coordinator: MyQDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []
    for serial_number, device in coordinator.data.items():
        # Connectivity sensor for all devices (openers and gateways)
        entities.append(MyQConnectivityBinarySensor(coordinator, serial_number))

        # Problem / Obstruction sensor for garage door openers
        if MyQClient.is_garage_door(device):
            entities.append(MyQProblemBinarySensor(coordinator, serial_number))

    async_add_entities(entities)


class MyQProblemBinarySensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], BinarySensorEntity
):
    """Problem / Obstruction binary sensor for myQ garage door openers."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize problem binary sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_problem"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return the friendly name of the problem sensor."""
        device = self.device_data
        base_name = device.get("name") or f"Garage Door {self._serial_number}"
        return f"{base_name} Problem"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo linking to the opener device."""
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
    def is_on(self) -> bool:
        """Return True if an obstruction, reversal, stall, or hardware fault is detected."""
        device = self.device_data
        state = device.get("state", {})
        door_state = MyQClient.get_door_state(device)

        # 1. Door stopped or auto-reversed
        if door_state in (STATE_AUTOREVERSE, STATE_STOPPED):
            return True

        # 2. 45-second stall timeout flagged by coordinator
        if self.coordinator.is_device_stalled(self._serial_number):
            return True

        # 3. Hardware / communication / battery faults
        if state.get("sensor_comm_error") is True:
            return True
        if state.get("dps_low_battery_mode") is True:
            return True
        if state.get("low_battery") is True:
            return True
        if state.get("attached_work_light_error_present") is True:
            return True

        # 4. Active fault codes list
        fault_codes = state.get("active_fault_codes")
        if isinstance(fault_codes, list) and len(fault_codes) > 0:
            return True

        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return fault and stall diagnostics."""
        device = self.device_data
        state = device.get("state", {})
        return {
            "door_state": MyQClient.get_door_state(device),
            "stalled": self.coordinator.is_device_stalled(self._serial_number),
            "sensor_comm_error": state.get("sensor_comm_error"),
            "dps_low_battery_mode": state.get("dps_low_battery_mode"),
            "low_battery": state.get("low_battery"),
            "attached_work_light_error_present": state.get(
                "attached_work_light_error_present"
            ),
            "active_fault_codes": state.get("active_fault_codes", []),
        }


class MyQConnectivityBinarySensor(
    CoordinatorEntity[MyQDataUpdateCoordinator], BinarySensorEntity
):
    """Cloud reachability and gateway online binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize connectivity binary sensor."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_connectivity"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name of connectivity sensor."""
        device = self.device_data
        base_name = device.get("name") or f"Device {self._serial_number}"
        return f"{base_name} Cloud Connectivity"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo linking to the device."""
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
    def is_on(self) -> bool:
        """Return True if device is online and coordinator is communicating successfully."""
        device = self.device_data
        is_online = MyQClient.is_device_online(device)
        return bool(is_online and self.coordinator.last_update_success)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return connectivity details."""
        state = self.device_data.get("state", {})
        return {
            "online": state.get("online"),
            "last_is_online_change": state.get("last_is_online_change"),
            "last_update": state.get("last_update"),
        }
