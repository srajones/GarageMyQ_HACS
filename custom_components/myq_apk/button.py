"""Button entity platform for Chamberlain/LiftMaster myQ integration.

Provides:
- "Refresh Status Now" button entity forcing an immediate coordinator refresh.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MyQClient
from .const import DOMAIN
from .coordinator import MyQDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up myQ button entities from config entry."""
    coordinator: MyQDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[MyQRefreshButton] = []
    for serial_number, device in coordinator.data.items():
        if MyQClient.is_garage_door(device):
            entities.append(MyQRefreshButton(coordinator, serial_number))

    async_add_entities(entities)


class MyQRefreshButton(
    CoordinatorEntity[MyQDataUpdateCoordinator], ButtonEntity
):
    """Button entity that triggers an immediate refresh of device states."""

    _attr_device_class = ButtonDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        serial_number: str,
    ) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_refresh_status"

    @property
    def device_data(self) -> dict[str, Any]:
        """Return raw device data from coordinator."""
        return self.coordinator.data.get(self._serial_number, {})

    @property
    def name(self) -> str:
        """Return friendly name."""
        device = self.device_data
        base_name = device.get("name") or f"Garage Door {self._serial_number}"
        return f"{base_name} Refresh Status Now"

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

    async def async_press(self) -> None:
        """Handle button press to force coordinator state refresh."""
        _LOGGER.info("Manual refresh triggered for device %s", self._serial_number)
        await self.coordinator.async_request_refresh()
