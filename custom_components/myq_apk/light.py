"""Light entity platform for Chamberlain/LiftMaster myQ opener work lights and smart lamps.

Conforms to Requirement R5 (unified DeviceInfo, light entity controls) and
authoritative specs in docs/PROTOCOL.md §8.3-8.4.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MyQClient
from .const import DOMAIN, LAMP_STATE_ON, MyQTimeoutError
from .coordinator import MyQDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def has_light_capability(device: dict[str, Any]) -> bool:
    """Return True if device is a lamp module or opener with work light capability."""
    if MyQClient.is_lamp(device):
        return True

    state = device.get("state", {})
    if isinstance(state, dict):
        if "lamp_state" in state:
            return True
        if "attached_work_light_error_present" in state:
            return True

    capabilities = device.get("capabilities", {})
    if isinstance(capabilities, dict):
        if capabilities.get("work_light") or capabilities.get("lamp"):
            return True

    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up myQ light entities from config entry."""
    coordinator: MyQDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[MyQWorkLight] = []
    for serial_number, device in coordinator.data.items():
        if has_light_capability(device):
            entities.append(MyQWorkLight(coordinator, serial_number))

    async_add_entities(entities)


class MyQWorkLight(CoordinatorEntity[MyQDataUpdateCoordinator], LightEntity):
    """Representation of a myQ opener work light or lamp switch."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_light"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return the friendly name of the light entity."""
        device = self.device_data
        base_name = device.get("name") or f"Device {self._serial_number}"
        if MyQClient.is_lamp(device):
            return base_name
        return f"{base_name} Work Light"

    @property
    def device_info(self) -> DeviceInfo:
        """Return unified DeviceInfo matching the parent opener or gateway."""
        device = self.device_data
        parent_serial = MyQClient.get_parent_serial_number(device)
        state = device.get("state", {})

        info = DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device.get("name") or f"Device {self._serial_number}",
            manufacturer="Chamberlain",
            model=device.get("device_model") or device.get("device_type") or "myQ Light",
            sw_version=state.get("firmware_version"),
        )
        if parent_serial and parent_serial != self._serial_number:
            info["via_device"] = (DOMAIN, parent_serial)
        return info

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        state = self.device_data.get("state", {})
        lamp_state = str(state.get("lamp_state", "")).strip().lower()
        return lamp_state in ("on", "true", "1")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the work light or lamp."""
        _LOGGER.info("Turning ON light for device %s", self._serial_number)
        try:
            await self.coordinator.client.set_light_state(self._serial_number, True)
            await self.coordinator.async_request_refresh()
        except MyQTimeoutError as exc:
            _LOGGER.warning(
                "Turn on command for light %s timed out; triggering refresh: %s",
                self._serial_number,
                exc,
            )
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.error("Failed to turn on light %s: %s", self._serial_number, exc)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the work light or lamp."""
        _LOGGER.info("Turning OFF light for device %s", self._serial_number)
        try:
            await self.coordinator.client.set_light_state(self._serial_number, False)
            await self.coordinator.async_request_refresh()
        except MyQTimeoutError as exc:
            _LOGGER.warning(
                "Turn off command for light %s timed out; triggering refresh: %s",
                self._serial_number,
                exc,
            )
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.error("Failed to turn off light %s: %s", self._serial_number, exc)
            raise
