"""Cover entity platform for Chamberlain/LiftMaster myQ garage doors.

Enforces physical safety invariants (Requirement R4):
1. Idempotent open/close gating (0 commands issued if already open/opening or closed/closing).
2. Timeout non-retry invariant (never re-issue timed out command payload; trigger state refresh).
3. Post-command fast-refresh bursts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MyQClient
from .const import (
    DOMAIN,
    STATE_AUTOREVERSE,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_STOPPED,
    STATE_UNKNOWN,
    MyQError,
    MyQTimeoutError,
)
from .coordinator import MyQDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up myQ garage door cover entities from config entry."""
    coordinator: MyQDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[MyQGarageDoor] = []
    for serial_number, device in coordinator.data.items():
        if MyQClient.is_garage_door(device):
            entities.append(MyQGarageDoor(coordinator, serial_number))

    async_add_entities(entities)


class MyQGarageDoor(CoordinatorEntity[MyQDataUpdateCoordinator], CoverEntity):
    """Representation of a Chamberlain / LiftMaster myQ garage door opener."""

    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize the garage door entity."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_cover"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data dictionary from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return the friendly name of the garage door."""
        device = self.device_data
        return device.get("name") or f"Garage Door {self._serial_number}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo linking all opener entities together."""
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
    def is_closed(self) -> Optional[bool]:
        """Return True if garage door is fully closed."""
        state = MyQClient.get_door_state(self.device_data)
        if state == STATE_CLOSED:
            return True
        if state in (
            STATE_OPEN,
            STATE_OPENING,
            STATE_CLOSING,
            STATE_STOPPED,
            STATE_AUTOREVERSE,
        ):
            return False
        return None

    @property
    def is_opening(self) -> bool:
        """Return True if garage door is actively opening."""
        return MyQClient.get_door_state(self.device_data) == STATE_OPENING

    @property
    def is_closing(self) -> bool:
        """Return True if garage door is actively closing."""
        return MyQClient.get_door_state(self.device_data) == STATE_CLOSING

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional telemetry and physical diagnostics."""
        device = self.device_data
        state = device.get("state", {})
        return {
            "door_state": MyQClient.get_door_state(device),
            "last_update": state.get("last_update"),
            "parent_serial_number": MyQClient.get_parent_serial_number(device),
            "is_unattended_open_allowed": state.get("is_unattended_open_allowed"),
            "is_unattended_close_allowed": state.get("is_unattended_close_allowed"),
            "dps_low_battery_mode": state.get("dps_low_battery_mode"),
            "active_fault_codes": state.get("active_fault_codes", []),
            "stalled": self.coordinator.is_device_stalled(self._serial_number),
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the garage door enforcing idempotency and timeout non-retry invariants."""
        current_state = MyQClient.get_door_state(self.device_data)

        # Idempotency Invariant: no-op if already open or opening (Requirement R4.1)
        if current_state in (STATE_OPEN, STATE_OPENING):
            _LOGGER.info(
                "Door %s is already %s; suppressing redundant open command (idempotency)",
                self._serial_number,
                current_state,
            )
            return

        _LOGGER.info("Sending open command to garage door %s", self._serial_number)
        try:
            await self.coordinator.client.open_door(self._serial_number)
            self.coordinator.start_fast_refresh(self._serial_number, STATE_OPEN)
            await self.coordinator.async_request_refresh()
        except MyQTimeoutError as exc:
            # Timeout Non-Retry Invariant: do NOT retry ambiguous command (Requirement R4.2)
            _LOGGER.warning(
                "Open command for door %s timed out; triggering refresh without re-issuing payload (safety invariant): %s",
                self._serial_number,
                exc,
            )
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.error("Failed to open garage door %s: %s", self._serial_number, exc)
            raise

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the garage door enforcing idempotency and timeout non-retry invariants."""
        current_state = MyQClient.get_door_state(self.device_data)

        # Idempotency Invariant: no-op if already closed or closing (Requirement R4.1)
        if current_state in (STATE_CLOSED, STATE_CLOSING):
            _LOGGER.info(
                "Door %s is already %s; suppressing redundant close command (idempotency)",
                self._serial_number,
                current_state,
            )
            return

        _LOGGER.info("Sending close command to garage door %s", self._serial_number)
        try:
            await self.coordinator.client.close_door(self._serial_number)
            self.coordinator.start_fast_refresh(self._serial_number, STATE_CLOSED)
            await self.coordinator.async_request_refresh()
        except MyQTimeoutError as exc:
            # Timeout Non-Retry Invariant: do NOT retry ambiguous command (Requirement R4.2)
            _LOGGER.warning(
                "Close command for door %s timed out; triggering refresh without re-issuing payload (safety invariant): %s",
                self._serial_number,
                exc,
            )
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.error("Failed to close garage door %s: %s", self._serial_number, exc)
            raise
