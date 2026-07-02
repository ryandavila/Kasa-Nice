"""Control of Kasa devices that only answer through the TP-Link cloud.

The HS300 power strips' firmware (from ~Oct 2025) replaced the local KLAP
credential handshake with a token/certificate scheme that python-kasa cannot yet
authenticate (upstream issue #1604), and disabled the legacy local port 9999.
The devices remain fully controllable through TP-Link's cloud — the same path the
Kasa mobile app uses — via the long-standing ``passthrough`` RPC, which tunnels
the original local JSON command (``get_sysinfo``, ``set_relay_state``) to the
device.

To avoid touching the rest of the app, the cloud devices here duck-type the small
slice of the python-kasa ``Device``/child interface that ``serialize_device`` and
``DeviceRegistry`` actually use: ``host``/``alias``/``model``/``device_type.name``/
``is_on``/``children`` plus ``update()`` and ``turn_on()``/``turn_off()``. They
slot straight into the existing registry, routes, and serializer.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import aiohttp
from kasa import Module

from .config import _DEFAULT_APP_TYPE, _DEFAULT_APP_VERSION, Settings, get_settings
from .logging_config import get_logger

logger = get_logger(__name__)

# Marker stored under modules[Module.Energy] so serialize_device reports the strip
# as having an energy meter. The real readings flow through energy_summary()
# rather than the python-kasa module interface, so this only needs to be non-None.
_EMETER_PRESENT = object()

# The unified TP-Link cloud rejects stale clients with error -23003 ("App version
# is too old"). The Kasa account is served by the same backend as Tapo, so we
# present as a recent Tapo Android client. The defaults (and their env overrides
# KASA_CLOUD_APP_TYPE/KASA_CLOUD_APP_VERSION) live in api.config; imported here so
# the KasaCloudClient signature can default to them.
_DEFAULT_BASE_URL = "https://wap.tplinkcloud.com"

# Cloud-level error codes that mean the token is stale and we should re-login.
_TOKEN_ERROR_CODES = {-20651, -20571, -20675, -20104}


class CloudError(RuntimeError):
    """A TP-Link cloud RPC returned a non-zero error_code."""


class KasaCloudClient:
    """Minimal async client for the TP-Link Kasa/Tapo cloud RPC endpoint."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        app_type: str = _DEFAULT_APP_TYPE,
        app_version: str = _DEFAULT_APP_VERSION,
        base_url: str = _DEFAULT_BASE_URL,
        terminal_uuid: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._username = username
        self._password = password
        self._app_type = app_type
        self._app_version = app_version
        self._base_url = base_url.rstrip("/")
        self._terminal_uuid = terminal_uuid or str(uuid.uuid4())
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._login_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"}
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _raw_post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        async with session.post(url, json=body, timeout=self._timeout) as resp:
            return await resp.json(content_type=None)

    async def _login(self) -> None:
        """Authenticate and cache a session token."""
        async with self._login_lock:
            body = {
                "method": "login",
                "params": {
                    "appType": self._app_type,
                    "appVersion": self._app_version,
                    "cloudUserName": self._username,
                    "cloudPassword": self._password,
                    "terminalUUID": self._terminal_uuid,
                },
            }
            data = await self._raw_post(self._base_url, body)
            if data.get("error_code") != 0:
                raise CloudError(
                    f"cloud login failed: error_code={data.get('error_code')} "
                    f"{data.get('msg', '')}".strip()
                )
            self._token = data["result"]["token"]
            logger.info("Authenticated with TP-Link cloud")

    async def _call(self, url_base: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST a token-authenticated RPC, re-logging in once if the token expired.

        Returns the ``result`` object. Raises ``CloudError`` on a hard failure.
        """
        if self._token is None:
            await self._login()
        for attempt in (1, 2):
            url = f"{url_base}/?token={self._token}"
            data = await self._raw_post(url, body)
            code = data.get("error_code")
            if code == 0:
                return data.get("result", {})
            if attempt == 1 and code in _TOKEN_ERROR_CODES:
                logger.info(f"Cloud token rejected (error_code={code}); re-logging in")
                self._token = None
                await self._login()
                continue
            raise CloudError(
                f"cloud RPC {body.get('method')} failed: error_code={code} "
                f"{data.get('msg', '')}".strip()
            )
        raise CloudError("cloud RPC failed after re-login")  # pragma: no cover

    async def get_device_list(self) -> list[dict[str, Any]]:
        result = await self._call(self._base_url, {"method": "getDeviceList"})
        return result.get("deviceList", [])

    async def passthrough(
        self, app_server_url: str, device_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        """Tunnel a local-style JSON command to a device and return its response."""
        body = {
            "method": "passthrough",
            "params": {
                "deviceId": device_id,
                "requestData": json.dumps(request),
            },
        }
        result = await self._call(app_server_url, body)
        return json.loads(result["responseData"])


def _norm_mac(mac: str) -> str:
    """Strip separators and upper-case a MAC for cross-source comparison."""
    return "".join(c for c in mac if c.isalnum()).upper()


def _format_mac(mac: str) -> str:
    """Render a normalized MAC as colon-separated pairs for display."""
    n = _norm_mac(mac)
    return ":".join(n[i : i + 2] for i in range(0, len(n), 2)) or n


def _entry_kwh(entry: dict[str, Any]) -> float:
    """Energy of one daystat/monthstat entry in kWh.

    Newer HS300 firmware reports integer watt-hours (``energy_wh``); older
    firmware reports kWh as a float (``energy``).
    """
    if "energy_wh" in entry:
        return entry["energy_wh"] / 1000
    return float(entry.get("energy", 0.0))


class CloudChild:
    """One outlet of a cloud-controlled power strip."""

    def __init__(self, device: CloudDevice, child_id: str, alias: str, is_on: bool):
        self._device = device
        self.child_id = child_id  # full TP-Link child id (deviceId + slot index)
        self.alias = alias
        self.is_on = is_on

    async def turn_on(self) -> None:
        await self._device._set_children_relay([self.child_id], True)

    async def turn_off(self) -> None:
        await self._device._set_children_relay([self.child_id], False)


class CloudDevice:
    """A python-kasa-shaped façade over a cloud-controlled device (e.g. HS300)."""

    def __init__(
        self,
        client: KasaCloudClient,
        *,
        device_id: str,
        app_server_url: str,
        mac: str,
        alias: str,
        model: str,
    ) -> None:
        self._client = client
        self._device_id = device_id
        self._app_server_url = app_server_url
        self.mac = _norm_mac(mac)
        self.alias = alias
        self.model = model
        # The public id is the normalized MAC (see ``stable_device_id``), shared
        # with locally-discovered devices so a device that migrates between local
        # and cloud control keeps one identity. host is connection/display only:
        # we prefer the device's LAN IP when the registry can resolve it from the
        # MAC, and fall back to the formatted MAC so something sensible shows
        # when it can't.
        self.host = _format_mac(mac)
        self.is_on = False
        self.children: list[CloudChild] = []
        # Once-guard so we warn about a drifted device clock at most once per
        # device, not on every usage poll.
        self._clock_drift_warned: bool = False
        # A strip is neither color nor dimmable, so the empty sys_info makes
        # serialize_device report both as false. It does meter energy per outlet,
        # so advertise an Energy module (read via energy_summary()).
        self.sys_info: dict[str, Any] = {}
        self.modules: dict[Any, Any] = {Module.Energy: _EMETER_PRESENT}
        self.device_type = SimpleNamespace(name="Strip")

    async def _passthrough(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._client.passthrough(
            self._app_server_url, self._device_id, request
        )

    async def update(self) -> None:
        """Refresh children and aggregate on/off state from a cloud get_sysinfo."""
        resp = await self._passthrough({"system": {"get_sysinfo": None}})
        sysinfo = resp["system"]["get_sysinfo"]
        self.alias = sysinfo.get("alias", self.alias)
        raw_children = sysinfo.get("children", [])
        by_id = {c.child_id: c for c in self.children}
        children: list[CloudChild] = []
        for rc in raw_children:
            cid, alias, on = rc["id"], rc.get("alias", ""), bool(rc.get("state"))
            existing = by_id.get(cid)
            if existing is not None:
                existing.alias, existing.is_on = alias, on
                children.append(existing)
            else:
                children.append(CloudChild(self, cid, alias, on))
        self.children = children
        # A strip is "on" if any outlet is on, matching python-kasa's semantics.
        self.is_on = any(c.is_on for c in self.children)

    async def _set_children_relay(self, child_ids: list[str], on: bool) -> None:
        await self._passthrough(
            {
                "context": {"child_ids": child_ids},
                "system": {"set_relay_state": {"state": 1 if on else 0}},
            }
        )
        wanted = set(child_ids)
        for child in self.children:
            if child.child_id in wanted:
                child.is_on = on
        self.is_on = any(c.is_on for c in self.children)

    async def turn_on(self) -> None:
        await self._set_children_relay([c.child_id for c in self.children], True)

    async def turn_off(self) -> None:
        await self._set_children_relay([c.child_id for c in self.children], False)

    async def _emeter(
        self, child_id: str, command: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        resp = await self._passthrough(
            {"context": {"child_ids": [child_id]}, "emeter": {command: params}}
        )
        return resp["emeter"][command]

    async def energy_summary(self) -> dict[str, Any]:
        """Aggregate every outlet's energy meter into one whole-strip summary.

        HS300 metering is per-outlet (a parent-level read returns only slot 0), so
        we read realtime power, this month's days, and this year's months for each
        outlet — concurrently — and sum them. Returns the keyword arguments the
        registry's ``Usage`` builder expects. On-demand only (driven by the usage
        endpoint), so it never runs on the state poll.
        """
        # The device buckets energy history by its OWN clock, which can drift from
        # the server's, so derive "today"/"this month" from the device itself —
        # otherwise today_kwh/month_kwh query an empty bucket. Fall back to the
        # server date if the clock read fails.
        try:
            clock = (await self._passthrough({"time": {"get_time": None}}))["time"][
                "get_time"
            ]
            year, month, today = clock["year"], clock["month"], clock["mday"]
            # The strip's internal clock can drift far behind real time, which
            # silently misattributes energy to the wrong day/month bucket. Warn
            # once if it's off by more than a day. Guarded so a malformed clock
            # can never break the normal return path.
            if not self._clock_drift_warned:
                try:
                    device_dt = datetime(
                        year,
                        month,
                        today,
                        clock.get("hour", 0),
                        clock.get("min", 0),
                        clock.get("sec", 0),
                    )
                    drift = datetime.now() - device_dt
                    if abs(drift) > timedelta(days=1):
                        self._clock_drift_warned = True
                        logger.warning(
                            f"Cloud device {self.host} clock is off by "
                            f"~{abs(drift).days} days (device reports "
                            f"{device_dt.date()}, server is "
                            f"{datetime.now().date()}); energy is attributed to "
                            "the wrong day/month. Resync the device clock in the "
                            "Kasa app."
                        )
                except Exception:  # noqa: BLE001 - never break on a bad clock
                    pass
        except Exception as e:  # noqa: BLE001 - fall back to the server clock
            logger.warning(f"Cloud device {self.host} clock read failed ({e})")
            now = datetime.now()
            year, month, today = now.year, now.month, now.day

        async def per_child(cid: str) -> tuple[dict, dict, dict]:
            return await asyncio.gather(
                self._emeter(cid, "get_realtime", {}),
                self._emeter(cid, "get_daystat", {"year": year, "month": month}),
                self._emeter(cid, "get_monthstat", {"year": year}),
            )

        results = await asyncio.gather(
            *(per_child(c.child_id) for c in self.children),
            return_exceptions=True,
        )

        total_power_mw = 0.0
        voltages: list[float] = []
        daily: dict[int, float] = {}
        monthly: dict[int, float] = {}
        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"Cloud emeter read failed for {self.host}: {result}")
                continue
            realtime, daystat, monthstat = result
            total_power_mw += realtime.get("power_mw") or 0
            if voltage_mv := realtime.get("voltage_mv"):
                voltages.append(voltage_mv / 1000)
            for entry in daystat.get("day_list", []):
                daily[entry["day"]] = daily.get(entry["day"], 0.0) + _entry_kwh(entry)
            for entry in monthstat.get("month_list", []):
                key = entry["month"]
                monthly[key] = monthly.get(key, 0.0) + _entry_kwh(entry)

        return {
            "current_power_w": total_power_mw / 1000,
            "voltage": sum(voltages) / len(voltages) if voltages else None,
            "today_kwh": daily.get(today),
            "month_kwh": monthly.get(month),
            "daily_raw": daily,
            "monthly_raw": monthly,
        }


async def discover_cloud_devices(
    client: KasaCloudClient,
    *,
    models: tuple[str, ...],
    skip_macs: frozenset[str] = frozenset(),
) -> list[CloudDevice]:
    """Build cloud-controlled devices for online account devices matching ``models``.

    ``skip_macs`` (normalized) lets the caller exclude devices it already controls
    locally, so a device reachable both ways isn't listed twice.
    """
    devices: list[CloudDevice] = []
    for entry in await client.get_device_list():
        model = entry.get("deviceModel", "")
        if not model.startswith(models):
            continue
        if entry.get("status") != 1:  # offline in the cloud's view
            logger.warning(
                f"Cloud device {entry.get('alias')!r} ({model}) is offline; skipping"
            )
            continue
        if _norm_mac(entry.get("deviceMac", "")) in skip_macs:
            continue
        device = CloudDevice(
            client,
            device_id=entry["deviceId"],
            app_server_url=entry["appServerUrl"],
            mac=entry.get("deviceMac", ""),
            alias=entry.get("alias", entry["deviceId"]),
            model=model,
        )
        try:
            await device.update()
        except Exception as e:  # noqa: BLE001 - one bad device shouldn't break the rest
            logger.error(f"Cloud device {device.alias!r} initial read failed: {e}")
            continue
        devices.append(device)
        logger.info(
            f"Attached cloud device {device.alias!r} ({model}) "
            f"with {len(device.children)} outlets"
        )
    return devices


def load_cloud_client(
    settings: Settings | None = None,
) -> tuple[KasaCloudClient, tuple[str, ...]] | None:
    """Build the cloud client + model filter from settings, if enabled.

    Cloud control is opt-in via ``KASA_CLOUD_FALLBACK`` (it sends credentials to
    TP-Link's servers, unlike local control). Reuses the primary TP-Link
    credentials. ``KASA_CLOUD_MODELS`` (default ``HS300``) restricts which device
    models are routed through the cloud. ``settings`` defaults to the shared
    instance; tests pass an isolated one.
    """
    settings = settings or get_settings()
    if not settings.kasa_cloud_fallback:
        return None
    username = settings.tplink_username
    password = settings.tplink_password
    if not (username and password):
        logger.warning(
            "KASA_CLOUD_FALLBACK is set but TPLINK_USERNAME/PASSWORD are not; "
            "cloud control disabled"
        )
        return None
    client = KasaCloudClient(
        username,
        password,
        app_type=settings.kasa_cloud_app_type,
        app_version=settings.kasa_cloud_app_version,
        terminal_uuid=settings.kasa_cloud_terminal_uuid,
    )
    return client, settings.cloud_models
