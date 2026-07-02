"""Discovery and control of Kasa devices, decoupled from any UI framework.

This is the logic that previously lived inside the NiceGUI page handlers in
``main.py``, reshaped into a small service the REST routes can call.
"""

import asyncio
import ipaddress
import time
from colorsys import hsv_to_rgb, rgb_to_hsv
from typing import Any

from kasa import Credentials, Discover, Module
from kasa import Device as KasaDevice
from kasa.exceptions import AuthenticationError

from .cloud_service import (
    KasaCloudClient,
    _norm_mac,
    discover_cloud_devices,
    load_cloud_client,
)
from .config import Settings, get_settings
from .device_store import HostStore
from .energy_history import EnergyHistoryStore, history
from .group_store import GroupStore, groups
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


def stable_device_id(device: KasaDevice) -> str:
    """The durable identity used to key a device, resilient to DHCP IP changes.

    Prefers the device's MAC — normalized (separators stripped, upper-cased) via
    the shared ``_norm_mac`` so locally- and cloud-discovered views of the same
    hardware agree — because a MAC is burned into the device and survives it being
    handed a new IP. Falls back to the LAN ``host`` only when no MAC is reported,
    preserving the historical behaviour for the rare device that lacks one. The
    ``host`` stays a separate field for connection and display.
    """
    mac = getattr(device, "mac", None)
    if mac:
        return _norm_mac(mac)
    return device.host


def stable_child_id(child: Any) -> str:
    """The durable identity of a strip outlet, resilient to it being renamed.

    python-kasa child devices expose ``device_id`` (the parent's id plus a slot
    suffix); the cloud façade's ``CloudChild`` exposes the equivalent ``child_id``.
    Either is stable across an outlet being renamed in the Kasa app, unlike the
    alias, and can't collide. Falls back to the alias only when no stable id is
    present (e.g. an older fake or firmware that omits it).
    """
    cid = getattr(child, "device_id", None) or getattr(child, "child_id", None)
    return cid or child.alias


class DeviceRegistry:
    """Holds the set of discovered devices and exposes control operations."""

    def __init__(
        self,
        store: HostStore | None = None,
        credentials: Credentials | None = None,
        *,
        cloud_client: KasaCloudClient | None = None,
        cloud_models: tuple[str, ...] = (),
        scan_subnet: str | None = None,
        energy_rate: float | None = None,
        energy_currency: str = "$",
        cloud_poll_interval: float = 30.0,
        group_store: GroupStore | None = None,
        history_store: EnergyHistoryStore | None = None,
    ) -> None:
        # Devices are keyed by their stable id (see ``stable_device_id``), NOT by
        # host, so a plug handed a new IP keeps its slot, history, room, and star.
        self._devices: dict[str, KasaDevice] = {}
        # Devices controlled through the TP-Link cloud (e.g. HS300 strips whose
        # firmware dropped local control). Kept separate so local re-discovery,
        # which rebuilds ``_devices``, never evicts them. They duck-type the
        # python-kasa interface, so the rest of the registry treats them alike.
        self._cloud_devices: dict[str, KasaDevice] = {}
        self._cloud_client = cloud_client
        self._cloud_models = cloud_models
        # Each cloud refresh is a round-trip to TP-Link's servers, so the state
        # poll refreshes cloud devices at most this often (seconds) instead of
        # every few seconds like local devices — keeping polls fast and avoiding
        # cloud rate limits. ``-inf`` forces a refresh on the first poll.
        self._cloud_poll_interval = cloud_poll_interval
        self._last_cloud_refresh: float = float("-inf")
        self._store = store
        # Passed to every Discover.discover() call. Newer SMART-protocol devices
        # authenticate before discovery; None (no creds) leaves legacy plugs
        # working and is equivalent to omitting the argument.
        self._credentials = credentials
        # Optional CIDR (e.g. "192.168.1.0/24") swept by unicast on startup and from
        # the Discovery tab — for devices on a separate subnet/VLAN that broadcast
        # discovery can't reach.
        self.scan_subnet = scan_subnet
        # Optional flat $/kWh rate (and its currency prefix) for showing energy
        # cost. A flat-rate APPROXIMATION — no tiered or time-of-use billing.
        # When energy_rate is None, cost fields stay null and nothing changes.
        self.energy_rate = energy_rate
        self.energy_currency = energy_currency
        # Hosts that answered discovery but couldn't be read (failed auth). Unlike
        # genuinely-offline hosts, these respond — so they're candidates for cloud
        # control, and we surface a hint about them when the fallback is off.
        self._unreadable_hosts: set[str] = set()
        # True while the initial network sweep is running. Exposed via /api/status
        # so the UI can show a "scanning…" state instead of an empty list — the
        # sweep is launched in the background so the API serves immediately.
        self.discovering: bool = False
        # Durable stores that keyed data by the old IP-as-id. When a device's
        # stable id is first learned this process, any of its data still filed
        # under the old host is lazily re-keyed (see ``_migrate_identity``). Left
        # None in tests that don't exercise migration, in which case it's a no-op.
        self._group_store = group_store
        self._history_store = history_store
        # Stable ids already migrated this process, so the one-time lazy migration
        # runs at most once per device rather than on every re-discovery.
        self._migrated_ids: set[str] = set()

    def _hosts(self) -> set[str]:
        """LAN hosts of the currently-cached local devices.

        The device dicts are keyed by stable id now, so the host store (which
        persists IPs to re-probe) and the cloud-IP resolver derive hosts from the
        device objects rather than from the dict keys.
        """
        return {d.host for d in self._devices.values()}

    def _migrate_identity(self, host: str, stable_id: str) -> None:
        """Re-key any durable data still filed under ``host`` to ``stable_id``.

        Rooms/favorites (``groups.json``) and the energy-history DB used to key a
        device by its LAN IP. Once we know a device's (host, stable-id) pair we
        rewrite those entries in place, so a plug that changed IP keeps its star,
        room, and energy chart. Runs at most once per device per process and is
        fully best-effort — a failure is logged and swallowed so it can never take
        down discovery. A no-op when the id already equals the host (no MAC, so
        nothing changed) or when no stores are wired in (tests).
        """
        if stable_id == host or stable_id in self._migrated_ids:
            return
        self._migrated_ids.add(stable_id)
        if self._group_store is not None:
            try:
                self._group_store.migrate_device_id(host, stable_id)
            except Exception as e:  # noqa: BLE001 - migration must never break discovery
                logger.warning(f"Group id migration {host} -> {stable_id} failed: {e}")
        if self._history_store is not None:
            try:
                self._history_store.migrate_device_id(host, stable_id)
            except Exception as e:  # noqa: BLE001 - migration must never break discovery
                logger.warning(f"Energy id migration {host} -> {stable_id} failed: {e}")

    async def _refresh(self, device: KasaDevice) -> bool:
        """Pull live state. Returns False if the device couldn't be read.

        An un-readable device (bad credentials, offline) has no usable data, so
        callers skip caching it — otherwise serializing it later would raise and
        take down the whole device list.
        """
        try:
            await device.update()
            return True
        except AuthenticationError as e:
            # Expected for devices that dropped local auth (e.g. HS300 strips):
            # the discovery caller logs a contextual warning, and they're handled
            # via the cloud fallback (or its hint), so this isn't a hard error.
            logger.debug(f"Local auth failed for {device.host}: {e}")
            return False
        except Exception as e:  # noqa: BLE001 - one bad device shouldn't break the rest
            logger.error(f"Error updating device {device.host}: {e}")
            return False

    @staticmethod
    async def _safe_disconnect(device: KasaDevice) -> None:
        """Release a dropped device's transport (its aiohttp session/connector).

        python-kasa opens a client session during discovery. When we discard a
        device — failed auth, offline, or probed only for its MAC — without
        disconnecting, that session is garbage-collected while still open and
        asyncio logs "Unclosed client session"/"Unclosed connector" at startup.
        Only ever called on devices we are NOT keeping, so it never closes a
        device still in the registry. Best-effort: a device that never connected
        raises, which we swallow.
        """
        disconnect = getattr(device, "disconnect", None)
        if disconnect is None:
            return
        try:
            await disconnect()
        except Exception as e:  # noqa: BLE001 - cleanup must never raise
            logger.debug(f"Disconnect of {getattr(device, 'host', '?')} failed: {e}")

    async def _store_device(self, device: KasaDevice) -> None:
        """Cache a readable device, disconnecting any object it supersedes.

        Re-discovery (e.g. the subnet sweep after a broadcast, or a manual
        re-scan) returns a *fresh* device object for a device we already hold. If
        we just overwrote the slot, the previous object's still-open aiohttp
        session would be orphaned and later log "Unclosed client session". So
        release the old one first. Keyed by the stable id, so the same physical
        device re-discovered at a new IP updates its existing slot rather than
        forking into a second identity.
        """
        stable_id = stable_device_id(device)
        old = self._devices.get(stable_id)
        if old is not None and old is not device:
            await self._safe_disconnect(old)
        self._devices[stable_id] = device
        # Now that the (host, stable-id) pair is known, fold any IP-keyed history
        # or room/favorite data onto the stable id (once per device per process).
        self._migrate_identity(device.host, stable_id)

    def _persist(self) -> None:
        """Save the union of known and currently-cached hosts.

        Hosts aren't dropped when temporarily offline, so a plug that's
        unplugged during a scan is still re-probed on the next startup.
        """
        if self._store is None:
            return
        self._store.save(self._store.load() | self._hosts())

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
                await self._store_device(device)
                self._unreadable_hosts.discard(device.host)
            else:
                self._unreadable_hosts.add(device.host)
                await self._safe_disconnect(device)
                logger.warning(
                    f"Known host {device.host} answered but could not be read "
                    "(bad credentials or offline); not serving it"
                )

    async def discover_all(self) -> list[KasaDevice]:
        """Broadcast-discover devices on the local network and cache them."""
        logger.info("Starting device discovery")
        discovered = await Discover.discover(credentials=self._credentials)
        self._unreadable_hosts = set()
        # A re-scan rebuilds the cache with fresh device objects; release the
        # previous ones so their aiohttp sessions don't leak as "Unclosed".
        previous = self._devices
        self._devices = {}
        for d in discovered.values():
            if await self._refresh(d):
                await self._store_device(d)
            else:
                self._unreadable_hosts.add(d.host)
                await self._safe_disconnect(d)

        # Re-attach known hosts that broadcast discovery didn't reach.
        if self._store is not None:
            for host in self._store.load() - self._hosts():
                await self._probe_host(host)

        # Devices from the prior scan that weren't re-cached are now orphaned;
        # disconnect them (by identity, never closing a device we kept).
        kept = set(map(id, self._devices.values()))
        for old in previous.values():
            if id(old) not in kept:
                await self._safe_disconnect(old)

        logger.info(f"Discovered {len(self._devices)} devices")
        self._persist()
        return list(self._devices.values())

    async def run_startup_discovery(self) -> None:
        """Run the full initial discovery, flagging ``discovering`` throughout.

        Broadcast discovery, an optional subnet sweep, and the cloud attach can
        together take many seconds, so this is launched as a background task at
        startup (the API serves immediately). The ``discovering`` flag lets the
        frontend show progress and surface devices as they appear, rather than a
        misleading empty list. Never raises — startup must not depend on it.
        """
        self.discovering = True
        try:
            await self.discover_all()
            if self.scan_subnet:
                await self.discover_subnet(self.scan_subnet)
            # After local discovery, so cloud devices already reachable locally
            # are skipped and the rest can be paired with their LAN IPs.
            await self.attach_cloud()
            self.log_cloud_fallback_hint()
        except Exception as e:  # noqa: BLE001 - never let startup discovery crash
            logger.error(f"Initial discovery failed: {e}")
        finally:
            self.discovering = False

    async def discover_target(self, target: str) -> list[KasaDevice]:
        """Probe a single IP (or broadcast address) and merge results in."""
        logger.info(f"Discovering target {target}")
        response = await Discover.discover(target=target, credentials=self._credentials)
        found: list[KasaDevice] = []
        for device in response.values():
            if await self._refresh(device):
                await self._store_device(device)
                self._unreadable_hosts.discard(device.host)
                found.append(device)
            else:
                self._unreadable_hosts.add(device.host)
                await self._safe_disconnect(device)
                logger.warning(
                    f"Device at {device.host} answered but could not be read "
                    "(bad credentials or offline)"
                )
        self._persist()
        return found

    async def discover_subnet(
        self, subnet: str, *, concurrency: int = 50, timeout: int = 2
    ) -> list[KasaDevice]:
        """Unicast-probe every host in a CIDR subnet and cache the readable ones.

        Broadcast discovery can't cross subnet/VLAN boundaries, so for devices on
        an isolated network (e.g. an IoT VLAN) we sweep each address directly.
        Raises ``ValueError`` if ``subnet`` is not a valid CIDR.
        """
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid subnet {subnet!r}: {e}") from e

        hosts = [str(h) for h in network.hosts()]
        logger.info(f"Sweeping {subnet} ({len(hosts)} hosts)")
        semaphore = asyncio.Semaphore(concurrency)
        found: list[KasaDevice] = []

        async def probe(host: str) -> None:
            async with semaphore:
                try:
                    device = await Discover.discover_single(
                        host,
                        credentials=self._credentials,
                        discovery_timeout=timeout,
                    )
                except Exception:  # noqa: BLE001 - most addresses have no device
                    return
                if await self._refresh(device):
                    await self._store_device(device)
                    self._unreadable_hosts.discard(device.host)
                    found.append(device)
                else:
                    self._unreadable_hosts.add(device.host)
                    await self._safe_disconnect(device)

        await asyncio.gather(*(probe(h) for h in hosts))
        logger.info(f"Subnet sweep of {subnet} found {len(found)} devices")
        self._persist()
        return found

    async def refresh_all(self) -> list[KasaDevice]:
        """Re-read live state from cached devices (no network discovery).

        Used by the frontend poll so the UI reflects changes made elsewhere
        (e.g. the Kasa app or a physical switch). Local devices refresh on every
        call; cloud devices are throttled to ``cloud_poll_interval`` seconds,
        since each cloud refresh is a TP-Link round-trip — polling them as often
        as local devices is slow and risks rate limiting. They remain in the
        returned list with their last-known state between refreshes. (Explicit
        actions like toggling a cloud outlet still refresh it immediately.)
        """
        to_refresh = list(self._devices.values())
        cloud = list(self._cloud_devices.values())
        now = time.monotonic()
        if cloud and now - self._last_cloud_refresh >= self._cloud_poll_interval:
            # Stamp before awaiting so an overlapping poll doesn't also refresh.
            self._last_cloud_refresh = now
            to_refresh += cloud
        await asyncio.gather(*(self._refresh(d) for d in to_refresh))
        return self.all()

    def all(self) -> list[KasaDevice]:
        return list(self._devices.values()) + list(self._cloud_devices.values())

    def get(self, device_id: str) -> KasaDevice:
        device = self._devices.get(device_id) or self._cloud_devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return device

    async def attach_cloud(self) -> list[KasaDevice]:
        """Discover and cache devices that are only controllable via the cloud.

        Excludes anything already controlled locally (matched by MAC), so a device
        reachable both ways isn't listed twice, and resolves each cloud device's
        LAN IP from its MAC when a known host advertises it, so it shows the same
        ``host`` as locally-discovered devices. Best-effort: never raises.
        """
        if self._cloud_client is None:
            return []
        try:
            local_macs = frozenset(
                _norm_mac(m)
                for d in self._devices.values()
                if (m := getattr(d, "mac", None))
            )
            cloud_devices = await discover_cloud_devices(
                self._cloud_client,
                models=self._cloud_models,
                skip_macs=local_macs,
            )
        except Exception as e:  # noqa: BLE001 - cloud must never break startup
            logger.error(f"Cloud device attach failed: {e}")
            return []

        mac_to_ip = await self._known_host_ips()
        self._cloud_devices = {}
        for device in cloud_devices:
            if (ip := mac_to_ip.get(device.mac)) is not None:
                device.host = ip
            # Keyed by the same stable id as local devices, so a device that moves
            # between local and cloud control keeps one identity (and its data).
            stable_id = stable_device_id(device)
            self._cloud_devices[stable_id] = device
            self._migrate_identity(device.host, stable_id)
        # State was just read during the attach, so reset the poll throttle.
        self._last_cloud_refresh = time.monotonic()
        logger.info(f"Attached {len(self._cloud_devices)} cloud device(s)")
        return list(self._cloud_devices.values())

    async def _known_host_ips(self) -> dict[str, str]:
        """Map normalized MAC -> LAN IP for known hosts not controlled locally.

        Discovery (unauthenticated) still reports a device's MAC even when its
        credentials are rejected, letting us pair a cloud device with its LAN IP.
        """
        if self._store is None:
            return {}
        unresolved = self._store.load() - self._hosts()
        result: dict[str, str] = {}

        async def probe(host: str) -> None:
            try:
                device = await Discover.discover_single(host)
            except Exception:  # noqa: BLE001 - host may be offline
                return
            if mac := getattr(device, "mac", None):
                result[_norm_mac(mac)] = host
            # Probed only for its MAC; release its session so it isn't left open.
            await self._safe_disconnect(device)

        await asyncio.gather(*(probe(h) for h in unresolved))
        return result

    def log_cloud_fallback_hint(self) -> None:
        """Nudge the user toward cloud control when it's off but would help.

        Logged only when the fallback is disabled and devices answered discovery
        yet failed local auth — the exact signature of a device (e.g. an HS300)
        whose firmware dropped local control. No-op when cloud control is already
        configured.
        """
        if self._cloud_client is not None or not self._unreadable_hosts:
            return
        hosts = ", ".join(sorted(self._unreadable_hosts))
        logger.warning(
            f"{len(self._unreadable_hosts)} known device(s) responded but failed "
            f"local authentication ({hosts}). If these dropped local control "
            "(e.g. HS300 strips), set KASA_CLOUD_FALLBACK=1 to control them via "
            "the TP-Link cloud."
        )

    async def aclose(self) -> None:
        """Release external resources at shutdown.

        Disconnects every cached device's aiohttp session (otherwise they log
        "Unclosed client session" during interpreter teardown) and closes the
        shared cloud HTTP session. Cloud devices share that session, so only the
        local devices need an individual disconnect.
        """
        await asyncio.gather(
            *(self._safe_disconnect(d) for d in self._devices.values())
        )
        self._devices = {}
        if self._cloud_client is not None:
            await self._cloud_client.close()

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
        """Energy-monitoring data for a device: live power plus history.

        Cloud devices supply their own aggregated summary (per-outlet meters
        rolled up); local devices are read through the python-kasa Energy module.
        """
        device = self.get(device_id)

        # Cloud devices meter per outlet and expose a ready-made summary. The
        # Usage is labelled with the stable ``device_id`` the caller looked up by,
        # so it matches the id under which history is recorded and served.
        summary = getattr(device, "energy_summary", None)
        if summary is not None:
            return _build_usage(device_id, rate=self.energy_rate, **await summary())

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

        return _build_usage(
            device_id,
            current_power_w=_safe("current_consumption"),
            today_kwh=_safe("consumption_today"),
            month_kwh=_safe("consumption_this_month"),
            voltage=_safe("voltage"),
            daily_raw=daily_raw,
            monthly_raw=monthly_raw,
            rate=self.energy_rate,
        )

    async def set_child_power(
        self, device_id: str, child_id: str, on: bool
    ) -> KasaDevice:
        """Toggle one outlet of a strip, matched by its stable child id.

        Matches on the stable id (``stable_child_id``); an alias match is kept as
        a fallback so ids saved by an older client (which addressed outlets by
        alias) keep working.
        """
        device = self.get(device_id)
        for child in device.children:
            if stable_child_id(child) == child_id or child.alias == child_id:
                await (child.turn_on() if on else child.turn_off())
                await self._refresh(device)
                return device
        raise DeviceNotFoundError(f"{device_id}/{child_id}")


def _cost(kwh: float | None, rate: float | None) -> float | None:
    """Money cost of ``kwh`` at a flat ``rate`` per kWh, rounded to cents.

    A flat-rate APPROXIMATION — it ignores tiered and time-of-use billing.
    Returns None when either the reading or the rate is absent, so cost fields
    simply stay null when no KASA_ENERGY_RATE is configured.
    """
    if kwh is None or rate is None:
        return None
    return round(kwh * rate, 2)


def _build_usage(
    device_id: str,
    *,
    current_power_w: float | None,
    today_kwh: float | None,
    month_kwh: float | None,
    voltage: float | None,
    daily_raw: dict[int, float],
    monthly_raw: dict[int, float],
    rate: float | None = None,
) -> Usage:
    """Assemble a Usage response from raw scalar readings and history maps.

    Shared by the local (python-kasa) and cloud energy paths so both label,
    round, and cost day/month history identically. ``rate`` is the optional
    flat $/kWh rate; when None, every cost field is null.
    """
    daily = [
        UsageStat(label=str(day), kwh=(k := round(kwh, 3)), cost=_cost(k, rate))
        for day, kwh in sorted(daily_raw.items())
    ]
    monthly = [
        UsageStat(
            label=_MONTHS[month - 1] if 1 <= month <= 12 else str(month),
            kwh=(k := round(kwh, 3)),
            cost=_cost(k, rate),
        )
        for month, kwh in sorted(monthly_raw.items())
    ]
    return Usage(
        device_id=device_id,
        current_power_w=current_power_w,
        today_kwh=today_kwh,
        month_kwh=month_kwh,
        today_cost=_cost(today_kwh, rate),
        month_cost=_cost(month_kwh, rate),
        voltage=voltage,
        daily=daily,
        monthly=monthly,
    )


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
        ChildPlug(id=stable_child_id(child), alias=child.alias, is_on=child.is_on)
        for child in device.children
    ]

    return Device(
        id=stable_device_id(device),
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


def _load_credentials(settings: Settings) -> Credentials | None:
    """Build TP-Link cloud credentials from settings, if provided.

    Newer SMART-protocol devices require these to be discovered or controlled.
    When unset, returns None so legacy plugs keep working without auth.
    """
    if settings.tplink_username and settings.tplink_password:
        return Credentials(settings.tplink_username, settings.tplink_password)
    logger.warning(
        "TPLINK_USERNAME/TPLINK_PASSWORD not set; only legacy (non-SMART) "
        "devices will be reachable"
    )
    return None


# Module-level singleton shared across requests, built from the shared settings.
# The host store lives at KASA_STATE_FILE (default ./data/known_devices.json) and
# the numeric knobs (energy rate, cloud poll interval) come parsed and validated
# from api.config; mount the state path as a volume to keep manually-added
# devices across container rebuilds.
_settings = get_settings()
_cloud = load_cloud_client(_settings)

registry = DeviceRegistry(
    HostStore(_settings.kasa_state_file),
    _load_credentials(_settings),
    cloud_client=_cloud[0] if _cloud else None,
    cloud_models=_cloud[1] if _cloud else (),
    scan_subnet=_settings.kasa_scan_subnet,
    energy_rate=_settings.kasa_energy_rate,
    energy_currency=_settings.kasa_energy_currency,
    cloud_poll_interval=_settings.kasa_cloud_poll_interval,
    # Wired in so the one-time lazy migration of IP-keyed rooms/favorites and
    # energy history can fire as devices are (re)discovered.
    group_store=groups,
    history_store=history,
)
