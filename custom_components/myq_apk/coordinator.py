"""DataUpdateCoordinator for Chamberlain/LiftMaster myQ integration.

Conforms to Requirement R3 (anti-lockout architecture, human-like polling cadence,
fast-refresh burst post-command, atomic token persistence) and Requirement R4
(stall, reversal, and obstruction tracking).
"""

from __future__ import annotations

from datetime import timedelta
import logging
import random
import time
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MyQClient
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_JITTER,
    FAST_POLL_BURST_MAX,
    FAST_POLL_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    STATE_AUTOREVERSE,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_STOPPED,
    TARGET_STATE_TIMEOUT,
    MyQAuthError,
    MyQError,
    MyQInvalidGrantError,
    MyQOfflineError,
    MyQRateLimitError,
    MyQTimeoutError,
)

_LOGGER = logging.getLogger(__name__)


class MyQDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """DataUpdateCoordinator managing human-like polling and fast bursts for myQ devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MyQClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.entry = entry

        # Polling & burst control state
        self._in_fast_burst = False
        self._fast_poll_target_serial: Optional[str] = None
        self._fast_poll_target_state: Optional[str] = None
        self._fast_poll_end_time: float = 0.0

        # Physical safety & stall tracking
        self._command_issue_time: dict[str, float] = {}
        self._command_target_state: dict[str, str] = {}
        self._stalled_devices: set[str] = set()

        # Register atomic rolling token persistence callback on client
        def _handle_token_refreshed(token_data: dict[str, Any]) -> None:
            _LOGGER.debug("Persisting refreshed tokens to ConfigEntry data")
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, **token_data},
            )

        self.client.on_token_refreshed = _handle_token_refreshed

        initial_interval = self._calculate_jittered_interval()
        super().__init__(
            hass,
            _LOGGER,
            name="myQ Data Coordinator",
            update_interval=timedelta(seconds=initial_interval),
        )

    def _calculate_jittered_interval(self) -> int:
        """Calculate next polling interval applying ±20s random jitter.

        Bounded between MIN_SCAN_INTERVAL (60s) and MAX_SCAN_INTERVAL (600s).
        """
        base = self.entry.options.get(
            CONF_SCAN_INTERVAL,
            self.entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        jitter = random.randint(-DEFAULT_SCAN_JITTER, DEFAULT_SCAN_JITTER)
        interval = max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, int(base) + jitter))
        return interval

    def start_fast_refresh(self, serial_number: str, target_state: str) -> None:
        """Trigger rapid post-command polling burst (5s interval for up to 35s)."""
        now = time.monotonic()
        self._command_issue_time[serial_number] = now
        self._command_target_state[serial_number] = target_state
        self._stalled_devices.discard(serial_number)

        self._fast_poll_target_serial = serial_number
        self._fast_poll_target_state = target_state
        self._fast_poll_end_time = now + FAST_POLL_BURST_MAX
        self._in_fast_burst = True
        self.update_interval = timedelta(seconds=FAST_POLL_INTERVAL)

        _LOGGER.debug(
            "Started fast-refresh burst for %s targeting '%s' (interval=%ss, max_burst=%ss)",
            serial_number,
            target_state,
            FAST_POLL_INTERVAL,
            FAST_POLL_BURST_MAX,
        )

    def is_device_stalled(self, serial_number: str) -> bool:
        """Return True if device is currently flagged as stalled or obstructed."""
        return serial_number in self._stalled_devices

    def clear_device_stall(self, serial_number: str) -> None:
        """Clear stall/obstruction flag for device."""
        self._stalled_devices.discard(serial_number)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch latest device list and update safety/stall tracking."""
        try:
            devices = await self.client.get_devices()
        except MyQInvalidGrantError as err:
            _LOGGER.error(
                "myQ OAuth refresh token permanently rejected: %s. Tripping circuit breaker.",
                err,
            )
            raise ConfigEntryAuthFailed(err) from err
        except MyQAuthError as err:
            _LOGGER.error("myQ authentication error: %s", err)
            raise ConfigEntryAuthFailed(err) from err
        except MyQRateLimitError as err:
            _LOGGER.warning("myQ rate limit encountered: %s", err)
            raise UpdateFailed(f"Rate limited by myQ cloud: {err}") from err
        except (MyQOfflineError, MyQTimeoutError) as err:
            _LOGGER.warning("myQ communication error: %s", err)
            raise UpdateFailed(f"Communication error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error communicating with myQ API: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

        device_map: dict[str, dict[str, Any]] = {
            str(dev["serial_number"]): dev
            for dev in devices
            if isinstance(dev, dict) and dev.get("serial_number")
        }

        # Track in-flight commands and stall / reversal safety conditions
        now = time.monotonic()
        for serial, issue_time in list(self._command_issue_time.items()):
            dev = device_map.get(serial)
            if not dev:
                continue

            target_state = self._command_target_state.get(serial)
            current_state = MyQClient.get_door_state(dev)

            # Reached target state
            if current_state == target_state:
                _LOGGER.info(
                    "Door %s successfully transitioned to target state '%s'",
                    serial,
                    target_state,
                )
                self._command_issue_time.pop(serial, None)
                self._command_target_state.pop(serial, None)
                self._stalled_devices.discard(serial)
                if serial == self._fast_poll_target_serial:
                    self._in_fast_burst = False

            # Auto-reverse or stopped mid-transit
            elif current_state in (STATE_AUTOREVERSE, STATE_STOPPED):
                _LOGGER.warning(
                    "Door %s stopped or autoreversed to '%s' during transit to '%s'",
                    serial,
                    current_state,
                    target_state,
                )
                self._stalled_devices.add(serial)
                self._command_issue_time.pop(serial, None)
                self._command_target_state.pop(serial, None)
                if serial == self._fast_poll_target_serial:
                    self._in_fast_burst = False

            # 45-second timeout exceeded without reaching target state
            elif (now - issue_time) >= TARGET_STATE_TIMEOUT:
                _LOGGER.warning(
                    "Door %s failed to reach target state '%s' within %ss (current state: '%s'); flagging stall/problem",
                    serial,
                    target_state,
                    TARGET_STATE_TIMEOUT,
                    current_state,
                )
                self._stalled_devices.add(serial)
                self._command_issue_time.pop(serial, None)
                self._command_target_state.pop(serial, None)
                if serial == self._fast_poll_target_serial:
                    self._in_fast_burst = False

        # Manage update interval cadence
        if self._in_fast_burst:
            if now >= self._fast_poll_end_time:
                _LOGGER.debug(
                    "Fast burst completed maximum duration (%ss); reverting to normal polling",
                    FAST_POLL_BURST_MAX,
                )
                self._in_fast_burst = False
                self._fast_poll_target_serial = None
                self._fast_poll_target_state = None
                self.update_interval = timedelta(seconds=self._calculate_jittered_interval())
            else:
                self.update_interval = timedelta(seconds=FAST_POLL_INTERVAL)
        else:
            self.update_interval = timedelta(seconds=self._calculate_jittered_interval())

        return device_map
