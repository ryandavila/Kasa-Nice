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
import os
import uuid
from types import SimpleNamespace
from typing import Any

import aiohttp

from .logging_config import get_logger

logger = get_logger(__name__)

# The unified TP-Link cloud rejects stale clients with error -23003 ("App version
# is too old"). The Kasa account is served by the same backend as Tapo, so we
# present as a recent Tapo Android client. Overridable via env if TP-Link bumps
# the minimum again.
_DEFAULT_APP_TYPE = "Tapo_Android"
_DEFAULT_APP_VERSION = "2.8.14"
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
        # host doubles as the public id in API paths (see serialize_device). We
        # prefer the device's LAN IP when the registry can resolve it from the
        # MAC, but fall back to the formatted MAC so the device is still
        # addressable when it isn't.
        self.host = _format_mac(mac)
        self.is_on = False
        self.children: list[CloudChild] = []
        # The rest of the app reads these but power strips have neither, so the
        # empty defaults make serialize_device report a non-color, non-dimmable,
        # non-emeter strip.
        self.sys_info: dict[str, Any] = {}
        self.modules: dict[Any, Any] = {}
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


def load_cloud_client() -> tuple[KasaCloudClient, tuple[str, ...]] | None:
    """Build the cloud client + model filter from the environment, if enabled.

    Cloud control is opt-in via ``KASA_CLOUD_FALLBACK`` (it sends credentials to
    TP-Link's servers, unlike local control). Reuses the primary TP-Link
    credentials. ``KASA_CLOUD_MODELS`` (default ``HS300``) restricts which device
    models are routed through the cloud.
    """
    if os.getenv("KASA_CLOUD_FALLBACK", "").lower() not in ("1", "true", "yes", "on"):
        return None
    username = os.getenv("TPLINK_USERNAME")
    password = os.getenv("TPLINK_PASSWORD")
    if not (username and password):
        logger.warning(
            "KASA_CLOUD_FALLBACK is set but TPLINK_USERNAME/PASSWORD are not; "
            "cloud control disabled"
        )
        return None
    models = tuple(
        m.strip()
        for m in os.getenv("KASA_CLOUD_MODELS", "HS300").split(",")
        if m.strip()
    )
    client = KasaCloudClient(
        username,
        password,
        app_type=os.getenv("KASA_CLOUD_APP_TYPE", _DEFAULT_APP_TYPE),
        app_version=os.getenv("KASA_CLOUD_APP_VERSION", _DEFAULT_APP_VERSION),
        terminal_uuid=os.getenv("KASA_CLOUD_TERMINAL_UUID"),
    )
    return client, models
