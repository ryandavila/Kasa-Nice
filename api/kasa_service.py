"""Discovery and control of Kasa devices, decoupled from any UI framework.

This is the logic that previously lived inside the NiceGUI page handlers in
``main.py``, reshaped into a small service the REST routes can call.
"""

import asyncio
import os
from colorsys import hsv_to_rgb, rgb_to_hsv
from pathlib import Path
from typing import Any

from kasa import Credentials, Discover, Module
from kasa import Device as KasaDevice

from .device_store import HostStore
from .logging_config import get_logger
from .schemas import ChildPlug, Device, Hsv, Usage, UsageStat

logger = get_logger(__name__)

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def hex_to_hsv(hex_color: str) -> Hsv:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    h, s, v = rgb_to_hsv(r / 255, g / 255, b / 255)
    return int(h * 360), int(s * 100), int(v * 100)


def hsv_to_hex(hsv: Hsv) -> str:
    h, s, v = hsv
    r, g, b = hsv_to_rgb(h / 360, s / 100, v / 100)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


class DeviceNotFoundError(KeyError):
    """Raised when a device or child id is not in the registry."""


class EnergyUnsupportedError(LookupError):
    """Raised when a device has no energy-monitoring (emeter) module."""


class DeviceRegistry:
    """Holds the set of discovered devices and exposes control operations."""

    def __init__(
        self,
        store: HostStore | None = None,
        credentials: Credentials | None = None,
    ) -> None:
        self._devices: dict[str, KasaDevice] = {}
        self._store = store
        # Passed to every Discover.discover() call. Newer SMART-protocol devices
        # authenticate before discovery; None (no creds) leaves legacy plugs
        # working and is equivalent to omitting the argument.
        self._credentials = credentials

    async def _refresh(self, device: KasaDevice) -> bool:
        """Pull live state. Returns False if the device couldn't be read.

        An un-readable device (bad credentials, offline) has no usable data, so
        callers skip caching it — otherwise serializing it later would raise and
        take down the whole device list.
        """
        try:
            await device.update()
            return True
        except Exception as e:  # noqa: BLE001 - one bad device shouldn't break the rest
            logger.error(f"Error updating device {device.host}: {e}")
            return False

    def _persist(self) -> None:
        """Save the union of known and currently-cached hosts.

        Hosts aren't dropped when temporarily offline, so a plug that's
        unplugged during a scan is still re-probed on the next startup.
        """
        if self._store is None:
            return
        self._store.save(self._store.load() | set(self._devices))

    async def _probe_host(self, host: str) -> None:
        """Re-attach a single known host, tolerating failure (it may be offline)."""
        try:
            response = await Discover.discover(
                target=host, credentials=self._credentials
            )
        except Exception as e:  # noqa: BLE001 - offline known host shouldn't break startup
            logger.warning(f"Known host {host} did not respond: {e}")
            return
        for device in response.values():
            if await self._refresh(device):
                self._devices[device.host] = device
            else:
                logger.warning(
                    f"Known host {device.host} answered but could not be read "
                    "(bad credentials or offline); not serving it"
                )

    async def discover_all(self) -> list[KasaDevice]:
        """Broadcast-discover devices on the local network and cache them."""
        logger.info("Starting device discovery")
        discovered = await Discover.discover(credentials=self._credentials)
        self._devices = {
            d.host: d for d in discovered.values() if await self._refresh(d)
        }

        # Re-attach known hosts that broadcast discovery didn't reach.
        if self._store is not None:
            for host in self._store.load() - set(self._devices):
                await self._probe_host(host)

        logger.info(f"Discovered {len(self._devices)} devices")
        self._persist()
        return list(self._devices.values())

    async def discover_target(self, target: str) -> list[KasaDevice]:
        """Probe a single IP (or broadcast address) and merge results in."""
        logger.info(f"Discovering target {target}")
        response = await Discover.discover(
            target=target, credentials=self._credentials
        )
        found: list[KasaDevice] = []
        for device in response.values():
            if await self._refresh(device):
                self._devices[device.host] = device
                found.append(device)
            else:
                logger.warning(
                    f"Device at {device.host} answered but could not be read "
                    "(bad credentials or offline)"
                )
        self._persist()
        return found

    async def refresh_all(self) -> list[KasaDevice]:
        """Re-read live state from cached devices (no network discovery).

        Used by the frontend poll so the UI reflects changes made elsewhere
        (e.g. the Kasa app or a physical switch).
        """
        await asyncio.gather(*(self._refresh(d) for d in self._devices.values()))
        return list(self._devices.values())

    def all(self) -> list[KasaDevice]:
        return list(self._devices.values())

    def get(self, device_id: str) -> KasaDevice:
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return device

    @staticmethod
    def _light(device: KasaDevice):
        light = device.modules.get(Module.Light)
        if light is None:
            raise DeviceNotFoundError(f"{device.host} has no light module")
        return light

    async def set_power(self, device_id: str, on: bool) -> KasaDevice:
        device = self.get(device_id)
        await (device.turn_on() if on else device.turn_off())
        await self._refresh(device)
        return device

    async def set_brightness(self, device_id: str, value: int) -> KasaDevice:
        device = self.get(device_id)
        await self._light(device).set_brightness(value)
        await self._refresh(device)
        return device

    async def set_hsv(self, device_id: str, hsv: Hsv) -> KasaDevice:
        device = self.get(device_id)
        await self._light(device).set_hsv(*hsv)
        await self._refresh(device)
        return device

    async def get_usage(self, device_id: str) -> Usage:
        """Energy-monitoring data for a device: live power plus history."""
        device = self.get(device_id)
        energy = device.modules.get(Module.Energy)
        if energy is None:
            raise EnergyUnsupportedError(device_id)
        await self._refresh(device)

        def _safe(name: str) -> float | None:
            try:
                value = getattr(energy, name)
            except Exception:  # noqa: BLE001 - missing reading shouldn't 500
                return None
            return float(value) if value is not None else None

        daily_raw: dict[int, float] = {}
        monthly_raw: dict[int, float] = {}
        try:
            daily_raw = await energy.get_daily_stats(kwh=True)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Daily stats for {device.host} failed: {e}")
        try:
            monthly_raw = await energy.get_monthly_stats(kwh=True)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Monthly stats for {device.host} failed: {e}")

        daily = [
            UsageStat(label=str(day), kwh=round(kwh, 3))
            for day, kwh in sorted(daily_raw.items())
        ]
        monthly = [
            UsageStat(
                label=_MONTHS[month - 1] if 1 <= month <= 12 else str(month),
                kwh=round(kwh, 3),
            )
            for month, kwh in sorted(monthly_raw.items())
        ]

        return Usage(
            device_id=device.host,
            current_power_w=_safe("current_consumption"),
            today_kwh=_safe("consumption_today"),
            month_kwh=_safe("consumption_this_month"),
            voltage=_safe("voltage"),
            daily=daily,
            monthly=monthly,
        )

    async def set_child_power(
        self, device_id: str, child_id: str, on: bool
    ) -> KasaDevice:
        device = self.get(device_id)
        for child in device.children:
            if child.alias == child_id:
                await (child.turn_on() if on else child.turn_off())
                await self._refresh(device)
                return device
        raise DeviceNotFoundError(f"{device_id}/{child_id}")


def serialize_device(device: KasaDevice) -> Device:
    """Convert a python-kasa device into the API schema."""
    sys_info: dict[str, Any] = getattr(device, "sys_info", {}) or {}
    is_color = bool(sys_info.get("is_color", 0))
    is_dimmable = bool(sys_info.get("is_dimmable", 0))

    brightness: int | None = None
    hsv: Hsv | None = None
    light = device.modules.get(Module.Light)
    if light is not None:
        if is_dimmable:
            brightness = light.brightness
        if is_color:
            hsv = tuple(light.hsv)  # type: ignore[assignment]

    children = [
        ChildPlug(id=child.alias, alias=child.alias, is_on=child.is_on)
        for child in device.children
    ]

    return Device(
        id=device.host,
        alias=device.alias or device.host,
        host=device.host,
        model=device.model,
        device_type=device.device_type.name,
        is_on=device.is_on,
        is_color=is_color,
        is_dimmable=is_dimmable,
        has_emeter=device.modules.get(Module.Energy) is not None,
        brightness=brightness,
        hsv=hsv,
        children=children,
    )


def _load_credentials() -> Credentials | None:
    """Build TP-Link cloud credentials from the environment, if provided.

    Newer SMART-protocol devices require these to be discovered or controlled.
    When unset, returns None so legacy plugs keep working without auth.
    """
    username = os.getenv("TPLINK_USERNAME")
    password = os.getenv("TPLINK_PASSWORD")
    if username and password:
        return Credentials(username, password)
    logger.warning(
        "TPLINK_USERNAME/TPLINK_PASSWORD not set; only legacy (non-SMART) "
        "devices will be reachable"
    )
    return None


# Module-level singleton shared across requests. The host store lives at
# KASA_STATE_FILE (default ./data/known_devices.json); mount that path as a
# volume to keep manually-added devices across container rebuilds.
_STATE_FILE = Path(os.getenv("KASA_STATE_FILE", "data/known_devices.json"))
registry = DeviceRegistry(HostStore(_STATE_FILE), _load_credentials())
