"""Discovery and control of Kasa devices, decoupled from any UI framework."""

import asyncio
import ipaddress
import time
from colorsys import hsv_to_rgb, rgb_to_hsv
from typing import Any, NamedTuple

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
from .device_store import DeviceSnapshotStore, HostStore
from .energy_history import EnergyHistoryStore, history
from .group_store import GroupStore, groups
from .logging_config import get_logger
from .schemas import ChildPlug, Device, Hsv, PowerResult, Usage, UsageStat

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


# Consecutive failed refreshes before a cached device reports unreachable. One
# miss is usually transient (wifi hiccup, momentary timeout); requiring a streak
# keeps a flaky-but-alive plug from flapping the UI and the unreachable alert.
_UNREACHABLE_AFTER_MISSES = 3


class DeviceNotFoundError(KeyError):
    """Raised when a device or child id is not in the registry."""


class EnergyUnsupportedError(LookupError):
    """Raised when a device has no energy-monitoring (emeter) module."""


class RenameUnsupportedError(RuntimeError):
    """Raised when a device/outlet can't be renamed through this API.

    The cloud façade exposes no ``set_alias``, so a rename of a cloud device is
    rejected up front rather than silently no-op'ing or hanging.
    """


def stable_device_id(device: KasaDevice) -> str:
    """The durable identity used to key a device, resilient to DHCP IP changes.

    Prefers the MAC — normalized via ``_norm_mac`` so local and cloud views of the
    same hardware agree — since it survives a new IP. Falls back to ``host`` only
    when no MAC is reported (``host`` stays a separate connection/display field).
    """
    mac = getattr(device, "mac", None)
    if mac:
        return _norm_mac(mac)
    return device.host


def stable_child_id(child: Any) -> str:
    """The durable identity of a strip outlet, resilient to it being renamed.

    python-kasa children expose ``device_id`` (parent id + slot); ``CloudChild``
    exposes the equivalent ``child_id``. Either is stable across a rename, unlike
    the alias. Falls back to the alias only when no stable id is present.
    """
    cid = getattr(child, "device_id", None) or getattr(child, "child_id", None)
    return cid or child.alias


class EnergySnapshot(NamedTuple):
    """The live scalars the background energy recorder persists.

    Just what ``EnergyHistoryStore`` stores (no history tables, no month total —
    months are recomputed from daily maxima), so ``read_energy_snapshot`` can
    skip the per-device stats fetches ``get_usage`` needs but the recorder
    would discard every cycle.
    """

    power_w: float | None
    today_kwh: float | None


class DeviceRegistry:
    """Holds the set of discovered devices and exposes control operations."""

    def __init__(
        self,
        store: HostStore | None = None,
        credentials: Credentials | None = None,
        *,
        snapshot_store: DeviceSnapshotStore | None = None,
        cloud_client: KasaCloudClient | None = None,
        cloud_models: tuple[str, ...] = (),
        scan_subnet: str | None = None,
        energy_rate: float | None = None,
        energy_currency: str = "$",
        cloud_poll_interval: float = 30.0,
        group_store: GroupStore | None = None,
        history_store: EnergyHistoryStore | None = None,
    ) -> None:
        # Keyed by stable id (see ``stable_device_id``), NOT host, so a plug handed
        # a new IP keeps its slot, history, room, and star.
        self._devices: dict[str, KasaDevice] = {}
        # Cloud-controlled devices (e.g. HS300 strips that dropped local control),
        # kept separate so local re-discovery (which rebuilds ``_devices``) can't
        # evict them. They duck-type the python-kasa interface.
        self._cloud_devices: dict[str, KasaDevice] = {}
        self._cloud_client = cloud_client
        self._cloud_models = cloud_models
        # Each cloud refresh is a TP-Link round-trip, so the state poll refreshes
        # cloud devices at most this often (seconds), keeping polls fast and
        # avoiding rate limits. ``-inf`` forces a refresh on the first poll.
        self._cloud_poll_interval = cloud_poll_interval
        self._last_cloud_refresh: float = float("-inf")
        self._store = store
        # Last-known identity per read device, keyed by host. Lets a device that
        # stops answering discovery show (grayed) from its snapshot. Held in memory
        # so serializing unreachable devices into every SSE frame never hits disk;
        # seeded from the store so snapshots survive a restart.
        self._snapshot_store = snapshot_store
        self._snapshots: dict[str, Device] = {}
        if snapshot_store is not None:
            for host, raw in snapshot_store.load().items():
                try:
                    self._snapshots[host] = Device(**raw)
                except Exception as e:  # noqa: BLE001 - a bad record must not break startup
                    logger.warning(f"Ignoring unreadable snapshot for {host}: {e}")
        # Passed to every Discover.discover(). SMART-protocol devices authenticate
        # before discovery; None leaves legacy plugs working.
        self._credentials = credentials
        # Optional CIDR swept by unicast for devices on a separate subnet/VLAN that
        # broadcast discovery can't reach.
        self.scan_subnet = scan_subnet
        # Optional flat $/kWh rate (and currency prefix) for energy cost — an
        # APPROXIMATION (no tiered/time-of-use billing). None => cost fields null.
        self.energy_rate = energy_rate
        self.energy_currency = energy_currency
        # Hosts that answered discovery but couldn't be read (failed auth). Unlike
        # offline hosts these respond, so they're cloud-control candidates and we
        # hint about them when the fallback is off.
        self._unreadable_hosts: set[str] = set()
        # True while the initial sweep runs; exposed via /api/status so the UI
        # shows "scanning…" instead of an empty list (the sweep runs in the
        # background so the API serves immediately).
        self.discovering: bool = False
        # Durable stores that once keyed data by IP-as-id; a device's IP-keyed data
        # is lazily re-keyed to its stable id on first sight (see
        # ``_migrate_identity``). None in tests => no-op.
        self._group_store = group_store
        self._history_store = history_store
        # Stable ids already migrated this process, so migration runs at most once
        # per device.
        self._migrated_ids: set[str] = set()
        # Consecutive failed refreshes per stable id. A cached device is only
        # rebuilt by discovery, so without this a device that dies mid-session
        # would serve stale state as reachable forever (no grayed card, no
        # unreachable alert). Any successful read clears the streak.
        self._miss_counts: dict[str, int] = {}
        # When the last full refresh_all completed, so secondary loops can skip
        # a refresh the SSE poll just did (see refresh_all_if_stale).
        self._last_refresh_all: float = float("-inf")

    def _hosts(self) -> set[str]:
        """LAN hosts of the currently-cached local devices.

        Derived from the device objects, not the dict keys (which are stable ids).
        """
        return {d.host for d in self._devices.values()}

    def _migrate_identity(self, host: str, stable_id: str) -> None:
        """Re-key any durable data still filed under ``host`` to ``stable_id``.

        Rooms/favorites and the energy-history DB once keyed by LAN IP; rewriting
        in place keeps a plug's star, room, and chart across an IP change. Runs at
        most once per device per process, best-effort (a failure is logged, never
        raised). No-op when the id equals the host or no stores are wired in.
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

        Callers skip caching an unreadable device (bad creds, offline) — else
        serializing it later would raise and take down the whole device list.
        Every outcome feeds the per-device miss streak behind ``is_reachable``,
        so any refresher (SSE loop, recorder, alerts, control actions) both
        detects an outage and clears one on recovery.
        """
        try:
            await device.update()
        except AuthenticationError as e:
            # Expected for devices that dropped local auth (e.g. HS300 strips);
            # handled via the cloud fallback, so not a hard error.
            logger.debug(f"Local auth failed for {device.host}: {e}")
            ok = False
        except Exception as e:  # noqa: BLE001 - one bad device shouldn't break the rest
            logger.error(f"Error updating device {device.host}: {e}")
            ok = False
        else:
            ok = True
        device_id = stable_device_id(device)
        if ok:
            self._miss_counts.pop(device_id, None)
        else:
            self._miss_counts[device_id] = self._miss_counts.get(device_id, 0) + 1
        return ok

    def is_reachable(self, device: KasaDevice) -> bool:
        """Whether a cached device is still answering its refreshes.

        False once ``_UNREACHABLE_AFTER_MISSES`` consecutive reads have failed;
        the serialized ``reachable`` flag and the unreachable/recovered alert
        edges key off this.
        """
        return (
            self._miss_counts.get(stable_device_id(device), 0)
            < _UNREACHABLE_AFTER_MISSES
        )

    @staticmethod
    async def _safe_disconnect(device: KasaDevice) -> None:
        """Release a dropped device's transport (its aiohttp session/connector).

        Discovery opens a client session; discarding a device without
        disconnecting leaks it as "Unclosed client session" at startup. Only
        called on devices we're NOT keeping. Best-effort: a device that never
        connected raises, which we swallow.
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

        Re-discovery returns a fresh object for a device we already hold;
        overwriting the slot would orphan the old object's open session
        ("Unclosed client session"), so release it first. Keyed by stable id, so
        the same device at a new IP updates its slot rather than forking.
        """
        stable_id = stable_device_id(device)
        old = self._devices.get(stable_id)
        if old is not None and old is not device:
            await self._safe_disconnect(old)
        self._devices[stable_id] = device
        # Fold any IP-keyed history/room/favorite data onto the stable id.
        self._migrate_identity(device.host, stable_id)
        # Refresh the last-known snapshot while readable, so a later drop-off can
        # still show identity. Flushed to disk by ``_persist`` at the end of the
        # pass, not here, so a subnet sweep doesn't rewrite the file per device.
        if self._snapshot_store is not None:
            self._snapshots[device.host] = self._snapshot_of(device)

    def _persist(self) -> None:
        """Save the union of known and currently-cached hosts.

        Offline hosts aren't dropped, so a plug unplugged during a scan is still
        re-probed on the next startup.
        """
        if self._store is None:
            return
        self._store.save(self._store.load() | self._hosts())
        # Flush the in-memory snapshots so an unreachable device survives a restart.
        if self._snapshot_store is not None:
            self._snapshot_store.save(
                {host: snap.model_dump() for host, snap in self._snapshots.items()}
            )

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
        # Re-scan rebuilds the cache with fresh objects; release the previous ones
        # so their sessions don't leak as "Unclosed".
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

        # Disconnect prior-scan devices that weren't re-cached (by identity, never
        # closing a device we kept).
        kept = set(map(id, self._devices.values()))
        for old in previous.values():
            if id(old) not in kept:
                await self._safe_disconnect(old)

        logger.info(f"Discovered {len(self._devices)} devices")
        self._persist()
        return list(self._devices.values())

    async def run_startup_discovery(self) -> None:
        """Run the full initial discovery, flagging ``discovering`` throughout.

        Launched as a background task at startup (broadcast + optional subnet
        sweep + cloud attach take many seconds; the API serves immediately). The
        ``discovering`` flag lets the frontend show progress. Never raises.
        """
        self.discovering = True
        try:
            await self.discover_all()
            if self.scan_subnet:
                await self.discover_subnet(self.scan_subnet)
            # After local discovery, so locally-reachable cloud devices are
            # skipped and the rest can be paired with their LAN IPs.
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

        Broadcast discovery can't cross subnet/VLAN boundaries, so sweep each
        address directly. Raises ``ValueError`` on an invalid CIDR.
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

        Local devices refresh every call; cloud devices are throttled to
        ``cloud_poll_interval`` seconds (each cloud refresh is a TP-Link
        round-trip) and keep their last-known state in the returned list between
        refreshes. Explicit actions still refresh a cloud device immediately.
        """
        to_refresh = list(self._devices.values())
        cloud = list(self._cloud_devices.values())
        now = time.monotonic()
        if cloud and now - self._last_cloud_refresh >= self._cloud_poll_interval:
            # Stamp before awaiting so an overlapping poll doesn't also refresh.
            self._last_cloud_refresh = now
            to_refresh += cloud
        await asyncio.gather(*(self._refresh(d) for d in to_refresh))
        self._last_refresh_all = time.monotonic()
        return self.all()

    async def refresh_all_if_stale(self, max_age: float) -> list[KasaDevice]:
        """``refresh_all()``, unless one already completed within ``max_age`` s.

        The SSE broadcaster refreshes every few seconds while a browser is open;
        loops with their own cadence (the alert evaluator) call this instead of
        ``refresh_all`` so they don't duplicate device round-trips that are
        already fresh.
        """
        if time.monotonic() - self._last_refresh_all >= max_age:
            return await self.refresh_all()
        return self.all()

    def all(self) -> list[KasaDevice]:
        return list(self._devices.values()) + list(self._cloud_devices.values())

    def _snapshot_of(self, device: KasaDevice) -> Device:
        """A neutralized identity snapshot of a currently-readable device.

        Reuses ``serialize_device`` for the identity fields but blanks volatile
        live state: a device served later from this snapshot is unreachable, so a
        stale on/brightness/color reading would be wrong. ``reachable`` is False.
        """
        live = serialize_device(device)
        return live.model_copy(
            update={
                "reachable": False,
                "is_on": False,
                "is_color": False,
                "is_dimmable": False,
                "has_emeter": False,
                "brightness": None,
                "hsv": None,
                "children": [
                    child.model_copy(update={"is_on": False}) for child in live.children
                ],
            }
        )

    @staticmethod
    def _host_only_snapshot(host: str) -> Device:
        """Fallback identity for a persisted host we've never successfully read.

        No snapshot means no MAC/alias, so surface the host as both. The id is the
        host — the same id ``stable_device_id`` gives a MAC-less device once read,
        so a room/favorite keyed to it stays stable. Deterministic, so repeated
        frames don't churn the id.
        """
        return Device(
            id=host,
            alias=host,
            host=host,
            model="",
            device_type="Unknown",
            is_on=False,
            is_color=False,
            is_dimmable=False,
            has_emeter=False,
            reachable=False,
        )

    def unreachable_devices(self) -> list[Device]:
        """Known devices that aren't currently live, as ``reachable=False`` entries.

        For each persisted host with no cached device, emit its last-known
        snapshot (or a host-only placeholder) so it stays visible as a grayed card
        instead of vanishing from rooms/favorites. Never touches the network.

        A device that changed IP is live under its stable id at the new host while
        its old IP lingers in the store; entries whose id is already live are
        skipped so it isn't forked into a phantom twin.
        """
        if self._store is None:
            return []
        live = self.all()
        live_hosts = {d.host for d in live}
        live_ids = {stable_device_id(d) for d in live}
        result: list[Device] = []
        for host in sorted(self._store.load() - live_hosts):
            snapshot = self._snapshots.get(host) or self._host_only_snapshot(host)
            if snapshot.id in live_ids:
                continue
            result.append(snapshot)
        return result

    def get(self, device_id: str) -> KasaDevice:
        device = self._devices.get(device_id) or self._cloud_devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return device

    def known_devices_export(self) -> tuple[list[str], dict[str, dict]]:
        """Persisted known hosts and identity snapshots, for backup.

        Reads straight from the durable stores (not live device objects, which
        aren't backup data), so this reflects exactly what a restart would
        re-probe. Empty when no stores are wired in (e.g. a test registry).
        """
        hosts = sorted(self._store.load()) if self._store is not None else []
        snapshots = (
            self._snapshot_store.load() if self._snapshot_store is not None else {}
        )
        return hosts, snapshots

    def restore_known_devices(
        self, hosts: list[str], snapshots: dict[str, dict]
    ) -> None:
        """Replace the persisted known-hosts/snapshot files from a backup.

        Deliberately does NOT touch live device connections (``_devices`` /
        ``_cloud_devices``) — those are real network sessions a JSON restore has
        no business tearing down. It writes through to disk and refreshes the
        in-memory snapshot cache (identity data only, safe to swap), so restored
        offline devices show up as grayed cards immediately; restored *hosts* are
        re-probed the next discovery sweep like any persisted host, not this
        instant, matching how the store is normally populated. No-op for a
        registry with no stores wired in (e.g. under ``KASA_FAKE_DEVICES``).
        """
        if self._store is not None:
            self._store.save(set(hosts))
        if self._snapshot_store is not None:
            self._snapshot_store.save(snapshots)
            self._snapshots = {}
            for host, raw in snapshots.items():
                try:
                    self._snapshots[host] = Device(**raw)
                except Exception as e:  # noqa: BLE001 - a bad record must not break restore
                    logger.warning(
                        f"Ignoring unreadable restored snapshot for {host}: {e}"
                    )

    async def attach_cloud(self) -> list[KasaDevice]:
        """Discover and cache devices that are only controllable via the cloud.

        Excludes anything already controlled locally (matched by MAC) so it isn't
        listed twice, and resolves each cloud device's LAN IP from its MAC so it
        shows the same ``host`` as local devices. Best-effort: never raises.
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
            # Same stable id as local devices, so one that moves between local and
            # cloud control keeps its identity (and data).
            stable_id = stable_device_id(device)
            self._cloud_devices[stable_id] = device
            self._migrate_identity(device.host, stable_id)
        # State was just read during the attach; reset the poll throttle.
        self._last_cloud_refresh = time.monotonic()
        logger.info(f"Attached {len(self._cloud_devices)} cloud device(s)")
        return list(self._cloud_devices.values())

    async def _known_host_ips(self) -> dict[str, str]:
        """Map normalized MAC -> LAN IP for known hosts not controlled locally.

        Discovery reports a device's MAC even when its credentials are rejected,
        letting us pair a cloud device with its LAN IP.
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
            # Probed only for its MAC; release the session.
            await self._safe_disconnect(device)

        await asyncio.gather(*(probe(h) for h in unresolved))
        return result

    def log_cloud_fallback_hint(self) -> None:
        """Nudge the user toward cloud control when it's off but would help.

        Logged only when the fallback is disabled and devices answered discovery
        yet failed local auth — the signature of a device (e.g. an HS300) that
        dropped local control. No-op when cloud control is configured.
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

        Disconnects each local device's session (else "Unclosed client session" at
        teardown) and closes the shared cloud HTTP session. Cloud devices share
        that session, so they need no individual disconnect.
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

        Cloud devices supply their own aggregated summary; local devices are read
        through the python-kasa Energy module.
        """
        device = self.get(device_id)

        # Cloud devices expose a ready-made per-outlet summary. Labelled with the
        # looked-up ``device_id`` so it matches the id history is recorded under.
        summary = getattr(device, "energy_summary", None)
        if summary is not None:
            return _build_usage(device_id, rate=self.energy_rate, **await summary())

        energy = device.modules.get(Module.Energy)
        if energy is None:
            raise EnergyUnsupportedError(device_id)
        await self._refresh(device)

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
            current_power_w=_safe_energy_value(energy, "current_consumption"),
            today_kwh=_safe_energy_value(energy, "consumption_today"),
            month_kwh=_safe_energy_value(energy, "consumption_this_month"),
            voltage=_safe_energy_value(energy, "voltage"),
            daily_raw=daily_raw,
            monthly_raw=monthly_raw,
            rate=self.energy_rate,
        )

    async def read_energy_snapshot(self, device_id: str) -> EnergySnapshot:
        """Cheap, scalar-only energy read for the background recorder.

        Persists only live power plus today's total, so unlike ``get_usage``
        this skips ``get_daily_stats``/``get_monthly_stats`` — the history-table
        fetches the recorder would discard every cycle. The scalars come from
        the Energy module's cached ``update()`` data. ``get_usage`` stays
        untouched: it still backs ``/usage``, which needs the tables.
        """
        device = self.get(device_id)

        # Cloud strips expose a scalar-only aggregate (``energy_scalars``) that
        # skips the per-outlet month-table reads a full summary does — a third
        # fewer rate-limited cloud RPCs per recorder cycle.
        scalars = getattr(device, "energy_scalars", None)
        if scalars is not None:
            data = await scalars()
            return EnergySnapshot(
                power_w=data.get("current_power_w"),
                today_kwh=data.get("today_kwh"),
            )

        energy = device.modules.get(Module.Energy)
        if energy is None:
            raise EnergyUnsupportedError(device_id)
        # A plain refresh populates the cached scalars; no history-table queries.
        await self._refresh(device)

        return EnergySnapshot(
            power_w=_safe_energy_value(energy, "current_consumption"),
            today_kwh=_safe_energy_value(energy, "consumption_today"),
        )

    async def set_child_power(
        self, device_id: str, child_id: str, on: bool
    ) -> KasaDevice:
        """Toggle one outlet of a strip, matched by its stable child id.

        Matches on the stable id (``stable_child_id``), with an alias fallback so
        ids saved by an older client (which addressed outlets by alias) still work.
        """
        device = self.get(device_id)
        for child in device.children:
            if stable_child_id(child) == child_id or child.alias == child_id:
                await (child.turn_on() if on else child.turn_off())
                await self._refresh(device)
                return device
        raise DeviceNotFoundError(f"{device_id}/{child_id}")

    async def set_alias(self, device_id: str, alias: str) -> KasaDevice:
        """Rename a device, then refresh so the new alias is reflected.

        Safe against our stable ids since they key on MAC/device_id, never the
        alias, so the client's id stays valid. Cloud-only devices have no
        ``set_alias``, so they're rejected rather than silently no-op'd.
        """
        device = self.get(device_id)
        rename = getattr(device, "set_alias", None)
        if rename is None:
            raise RenameUnsupportedError(device_id)
        await rename(alias)
        await self._refresh(device)
        return device

    async def set_child_alias(
        self, device_id: str, child_id: str, alias: str
    ) -> KasaDevice:
        """Rename one outlet of a strip, matched by its stable child id.

        Matches like ``set_child_power`` (stable id, alias fallback). The child id
        doesn't derive from the alias, so renaming can't change which outlet it
        points at. Returns the parent so the whole strip is serialized.
        """
        device = self.get(device_id)
        for child in device.children:
            if stable_child_id(child) == child_id or child.alias == child_id:
                rename = getattr(child, "set_alias", None)
                if rename is None:
                    raise RenameUnsupportedError(f"{device_id}/{child_id}")
                await rename(alias)
                await self._refresh(device)
                return device
        raise DeviceNotFoundError(f"{device_id}/{child_id}")


def _safe_energy_value(energy, name: str) -> float | None:
    """Read one scalar off the Energy module, degrading to None.

    A reading a device doesn't provide (or a module mid-update) must not fail
    the caller — ``/usage`` shouldn't 500 and a recorder cycle shouldn't stop.
    """
    try:
        value = getattr(energy, name)
    except Exception:  # noqa: BLE001 - a missing reading is data, not an error
        return None
    return float(value) if value is not None else None


def partition_results(keys: list[str], results: list) -> tuple[list[str], list[str]]:
    """Split ``gather(return_exceptions=True)`` output into (succeeded, failed).

    Shared by every fan-out that tolerates per-device failure (room/global
    power, scene apply) so the partition idiom exists once.
    """
    succeeded: list[str] = []
    failed: list[str] = []
    for key, result in zip(keys, results, strict=True):
        (failed if isinstance(result, Exception) else succeeded).append(key)
    return succeeded, failed


async def set_power_many(
    registry: DeviceRegistry, device_ids: list[str], on: bool
) -> PowerResult:
    """Switch many devices concurrently, tolerating per-device failure.

    A device that errors or no longer exists is reported under ``failed``
    instead of aborting the batch. Service-level so the routes, the scheduler,
    and room fan-outs share one implementation; callers publish the SSE update
    afterwards.
    """
    results = await asyncio.gather(
        *(registry.set_power(device_id, on) for device_id in device_ids),
        return_exceptions=True,
    )
    succeeded, failed = partition_results(device_ids, results)
    return PowerResult(on=on, succeeded=succeeded, failed=failed)


def _cost(kwh: float | None, rate: float | None) -> float | None:
    """Money cost of ``kwh`` at a flat ``rate`` per kWh, rounded to cents.

    A flat-rate APPROXIMATION (no tiered/time-of-use billing). None when either
    the reading or rate is absent, so cost fields stay null with no rate set.
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

    Shared by the local and cloud energy paths so both label, round, and cost
    history identically. ``rate`` None => every cost field null.
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


def serialize_device(device: KasaDevice, *, reachable: bool = True) -> Device:
    """Convert a python-kasa device into the API schema.

    ``reachable`` lets list/stream callers mark a cached device whose refreshes
    keep failing (see ``DeviceRegistry.is_reachable``); the identity and last
    cached state still serialize so the card grays out in place.
    """
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
        reachable=reachable,
        # python-kasa devices expose set_alias; the cloud façade doesn't, so its
        # absence means "can't be renamed here". A strip is uniformly local or
        # cloud, so this flag also gates its per-outlet rename in the UI.
        can_rename=hasattr(device, "set_alias"),
    )


def _load_credentials(settings: Settings) -> Credentials | None:
    """Build TP-Link cloud credentials from settings, if provided.

    SMART-protocol devices need these; None when unset so legacy plugs still work.
    """
    if settings.tplink_username and settings.tplink_password:
        return Credentials(settings.tplink_username, settings.tplink_password)
    logger.warning(
        "TPLINK_USERNAME/TPLINK_PASSWORD not set; only legacy (non-SMART) "
        "devices will be reachable"
    )
    return None


# Module-level singleton built from the shared settings. Host store at
# KASA_STATE_FILE (default ./data/known_devices.json); mount that path as a volume
# to keep manually-added devices.
_settings = get_settings()
_cloud = load_cloud_client(_settings)

registry = DeviceRegistry(
    HostStore(_settings.kasa_state_file),
    _load_credentials(_settings),
    # Lets known-but-offline devices stay visible (grayed) instead of vanishing.
    snapshot_store=DeviceSnapshotStore(_settings.kasa_snapshot_file),
    cloud_client=_cloud[0] if _cloud else None,
    cloud_models=_cloud[1] if _cloud else (),
    scan_subnet=_settings.kasa_scan_subnet,
    energy_rate=_settings.kasa_energy_rate,
    energy_currency=_settings.kasa_energy_currency,
    cloud_poll_interval=_settings.kasa_cloud_poll_interval,
    # Wired in so the one-time IP-keyed data migration can fire on discovery.
    group_store=groups,
    history_store=history,
)
