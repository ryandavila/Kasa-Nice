"""Tests for the TP-Link cloud control path (``api.cloud_service``).

The HTTP boundary is the only thing mocked: ``KasaCloudClient._raw_post`` (the
single method that actually talks to the network) is patched with an
``AsyncMock``, and ``CloudDevice`` is driven through a fake client whose
``passthrough``/``get_device_list`` are ``AsyncMock``s. No real devices or
network are involved, matching the rest of the suite. Tests use ``asyncio.run``
rather than a plugin, consistent with ``test_kasa_service.py``.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import kasa_service
from api.cloud_service import (
    CloudChild,
    CloudDevice,
    CloudError,
    KasaCloudClient,
    _entry_kwh,
    _format_mac,
    _norm_mac,
    discover_cloud_devices,
    load_cloud_client,
)
from api.kasa_service import DeviceRegistry, _build_usage

# ── helpers ──────────────────────────────────────────────────────────────────


def test_norm_mac_strips_separators_and_uppercases():
    assert _norm_mac("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"
    assert _norm_mac("AA-BB-CC-DD-EE-FF") == "AABBCCDDEEFF"


def test_format_mac_renders_colon_pairs():
    assert _format_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert _format_mac("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"
    assert _format_mac("") == ""


def test_entry_kwh_prefers_integer_watt_hours():
    assert _entry_kwh({"energy_wh": 1500}) == 1.5


def test_entry_kwh_falls_back_to_float_kwh():
    assert _entry_kwh({"energy": 2.5}) == 2.5


def test_entry_kwh_defaults_to_zero():
    assert _entry_kwh({}) == 0.0


# ── KasaCloudClient: login ───────────────────────────────────────────────────


def _client() -> KasaCloudClient:
    return KasaCloudClient("user@example.com", "pw", terminal_uuid="fixed-uuid")


def test_login_caches_token_and_sends_credentials():
    client = _client()
    client._raw_post = AsyncMock(
        return_value={"error_code": 0, "result": {"token": "TOK"}}
    )

    asyncio.run(client._login())

    assert client._token == "TOK"
    (_url, body), _ = client._raw_post.call_args
    assert body["method"] == "login"
    assert body["params"]["cloudUserName"] == "user@example.com"
    assert body["params"]["cloudPassword"] == "pw"
    # Presents a current client identity so the cloud doesn't reject as too old.
    assert body["params"]["appType"] == "Tapo_Android"


def test_login_raises_cloud_error_with_code():
    client = _client()
    client._raw_post = AsyncMock(
        return_value={"error_code": -23003, "msg": "App version is too old"}
    )

    with pytest.raises(CloudError, match="-23003"):
        asyncio.run(client._login())
    assert client._token is None


# ── KasaCloudClient: _call (token auth + re-login) ───────────────────────────


def test_call_returns_result_on_success():
    client = _client()
    client._token = "TOK"
    client._raw_post = AsyncMock(
        return_value={"error_code": 0, "result": {"value": 42}}
    )

    result = asyncio.run(client._call("https://srv", {"method": "x"}))

    assert result == {"value": 42}


def test_call_relogs_in_once_on_token_error():
    client = _client()
    client._token = "STALE"
    # 1: token-expired error, 2: login (new token), 3: success on retry.
    client._raw_post = AsyncMock(
        side_effect=[
            {"error_code": -20651, "msg": "token expired"},
            {"error_code": 0, "result": {"token": "FRESH"}},
            {"error_code": 0, "result": {"ok": True}},
        ]
    )

    result = asyncio.run(client._call("https://srv", {"method": "x"}))

    assert result == {"ok": True}
    assert client._token == "FRESH"
    assert client._raw_post.await_count == 3


def test_call_raises_on_non_token_error_without_relogin():
    client = _client()
    client._token = "TOK"
    client._raw_post = AsyncMock(return_value={"error_code": -1, "msg": "boom"})

    with pytest.raises(CloudError, match="boom"):
        asyncio.run(client._call("https://srv", {"method": "x"}))
    assert client._raw_post.await_count == 1  # no re-login attempted


# ── KasaCloudClient: getDeviceList + passthrough ─────────────────────────────


def test_get_device_list_parses_payload():
    client = _client()
    client._token = "TOK"
    client._raw_post = AsyncMock(
        return_value={
            "error_code": 0,
            "result": {"deviceList": [{"deviceId": "A"}, {"deviceId": "B"}]},
        }
    )

    devices = asyncio.run(client.get_device_list())

    assert [d["deviceId"] for d in devices] == ["A", "B"]


def test_get_device_list_defaults_to_empty():
    client = _client()
    client._token = "TOK"
    client._raw_post = AsyncMock(return_value={"error_code": 0, "result": {}})

    assert asyncio.run(client.get_device_list()) == []


def test_passthrough_stringifies_request_and_parses_response():
    client = _client()
    client._token = "TOK"
    inner_response = {"system": {"get_sysinfo": {"alias": "Strip"}}}
    client._raw_post = AsyncMock(
        return_value={
            "error_code": 0,
            "result": {"responseData": json.dumps(inner_response)},
        }
    )

    request = {"system": {"get_sysinfo": None}}
    parsed = asyncio.run(client.passthrough("https://srv", "DEV", request))

    # Response JSON string round-tripped back into a dict.
    assert parsed == inner_response
    # Request was JSON-stringified into requestData and round-trips back.
    (_url, body), _ = client._raw_post.call_args
    assert body["method"] == "passthrough"
    assert body["params"]["deviceId"] == "DEV"
    assert json.loads(body["params"]["requestData"]) == request


# ── CloudDevice ──────────────────────────────────────────────────────────────


def _cloud_device(passthrough: AsyncMock) -> CloudDevice:
    client = SimpleNamespace(passthrough=passthrough)
    return CloudDevice(
        client,
        device_id="DEV",
        app_server_url="https://srv",
        mac="aa:bb:cc:dd:ee:ff",
        alias="Strip",
        model="HS300",
    )


def _sysinfo(children: list[dict]) -> dict:
    return {"system": {"get_sysinfo": {"alias": "Strip", "children": children}}}


def test_update_parses_children_and_is_on_when_any_outlet_on():
    passthrough = AsyncMock(
        return_value=_sysinfo(
            [
                {"id": "c0", "alias": "Outlet 1", "state": 1},
                {"id": "c1", "alias": "Outlet 2", "state": 0},
            ]
        )
    )
    device = _cloud_device(passthrough)

    asyncio.run(device.update())

    assert [c.alias for c in device.children] == ["Outlet 1", "Outlet 2"]
    assert [c.is_on for c in device.children] == [True, False]
    assert device.is_on is True  # any outlet on


def test_update_is_off_when_all_outlets_off():
    passthrough = AsyncMock(
        return_value=_sysinfo([{"id": "c0", "alias": "O", "state": 0}])
    )
    device = _cloud_device(passthrough)

    asyncio.run(device.update())

    assert device.is_on is False


def test_update_reuses_existing_child_objects_by_id():
    passthrough = AsyncMock(
        return_value=_sysinfo([{"id": "c0", "alias": "O", "state": 0}])
    )
    device = _cloud_device(passthrough)

    asyncio.run(device.update())
    first = device.children[0]
    asyncio.run(device.update())

    assert device.children[0] is first  # same child id -> same object


def test_turn_on_off_update_local_state():
    passthrough = AsyncMock(return_value={})
    device = _cloud_device(passthrough)
    device.children = [
        CloudChild(device, "c0", "Outlet 1", False),
        CloudChild(device, "c1", "Outlet 2", False),
    ]

    asyncio.run(device.turn_on())
    assert all(c.is_on for c in device.children)
    assert device.is_on is True

    asyncio.run(device.turn_off())
    assert not any(c.is_on for c in device.children)
    assert device.is_on is False


def test_child_turn_on_targets_one_outlet():
    passthrough = AsyncMock(return_value={})
    device = _cloud_device(passthrough)
    device.children = [
        CloudChild(device, "c0", "Outlet 1", False),
        CloudChild(device, "c1", "Outlet 2", False),
    ]

    asyncio.run(device.children[0].turn_on())

    assert device.children[0].is_on is True
    assert device.children[1].is_on is False
    assert device.is_on is True
    # The relay command carried only the targeted child id.
    (_url, _dev, request), _ = passthrough.call_args
    assert request["context"]["child_ids"] == ["c0"]


def test_energy_summary_sums_per_outlet_using_device_clock():
    def route(_app_server_url, _device_id, request):
        if "time" in request:
            return {
                "time": {
                    "get_time": {
                        "year": 2026,
                        "month": 3,
                        "mday": 6,
                        "hour": 10,
                        "min": 0,
                        "sec": 0,
                    }
                }
            }
        emeter = request["emeter"]
        if "get_realtime" in emeter:
            return {
                "emeter": {"get_realtime": {"power_mw": 5000, "voltage_mv": 120000}}
            }
        if "get_daystat" in emeter:
            return {
                "emeter": {
                    "get_daystat": {
                        "day_list": [
                            {"day": 6, "energy_wh": 100},
                            {"day": 5, "energy_wh": 50},
                        ]
                    }
                }
            }
        if "get_monthstat" in emeter:
            return {
                "emeter": {
                    "get_monthstat": {"month_list": [{"month": 3, "energy_wh": 2000}]}
                }
            }
        raise AssertionError(f"unexpected request {request}")

    passthrough = AsyncMock(side_effect=route)
    device = _cloud_device(passthrough)
    device.children = [
        CloudChild(device, "c0", "Outlet 1", True),
        CloudChild(device, "c1", "Outlet 2", False),
    ]

    summary = asyncio.run(device.energy_summary())

    # Summed across the two outlets.
    assert summary["current_power_w"] == pytest.approx(10.0)  # 2 × 5000 mW
    assert summary["voltage"] == pytest.approx(120.0)
    # "today"/"this month" come from the device clock (mday=6, month=3).
    assert summary["today_kwh"] == pytest.approx(0.2)  # 2 × 0.1 kWh
    assert summary["month_kwh"] == pytest.approx(4.0)  # 2 × 2.0 kWh
    assert summary["daily_raw"][6] == pytest.approx(0.2)
    assert summary["daily_raw"][5] == pytest.approx(0.1)
    assert summary["monthly_raw"][3] == pytest.approx(4.0)


# ── discover_cloud_devices ───────────────────────────────────────────────────


def _list_client(
    entries: list[dict], *, sysinfo: dict | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        get_device_list=AsyncMock(return_value=entries),
        passthrough=AsyncMock(return_value=sysinfo or _sysinfo([])),
    )


def _entry(**over) -> dict:
    base = {
        "deviceModel": "HS300(US)",
        "status": 1,
        "deviceMac": "AA:BB:CC:DD:EE:01",
        "deviceId": "DEV1",
        "appServerUrl": "https://srv",
        "alias": "Strip",
    }
    base.update(over)
    return base


def test_discover_filters_by_model():
    client = _list_client([_entry(), _entry(deviceModel="KP125M", deviceId="DEV2")])

    devices = asyncio.run(discover_cloud_devices(client, models=("HS300",)))

    assert [d.model for d in devices] == ["HS300(US)"]


def test_discover_skips_offline_devices():
    client = _list_client([_entry(status=0)])

    assert asyncio.run(discover_cloud_devices(client, models=("HS300",))) == []


def test_discover_skips_macs_already_local():
    client = _list_client([_entry(deviceMac="AA:BB:CC:DD:EE:01")])

    devices = asyncio.run(
        discover_cloud_devices(
            client, models=("HS300",), skip_macs=frozenset({"AABBCCDDEE01"})
        )
    )

    assert devices == []


def test_discover_skips_device_whose_initial_read_fails():
    client = SimpleNamespace(
        get_device_list=AsyncMock(return_value=[_entry()]),
        passthrough=AsyncMock(side_effect=RuntimeError("cloud down")),
    )

    assert asyncio.run(discover_cloud_devices(client, models=("HS300",))) == []


# ── load_cloud_client (env parsing) ──────────────────────────────────────────


def test_load_cloud_client_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KASA_CLOUD_FALLBACK", raising=False)
    assert load_cloud_client() is None


def test_load_cloud_client_enabled_without_credentials_returns_none(monkeypatch):
    monkeypatch.setenv("KASA_CLOUD_FALLBACK", "1")
    monkeypatch.delenv("TPLINK_USERNAME", raising=False)
    monkeypatch.delenv("TPLINK_PASSWORD", raising=False)
    assert load_cloud_client() is None


def test_load_cloud_client_builds_client_and_default_models(monkeypatch):
    monkeypatch.setenv("KASA_CLOUD_FALLBACK", "yes")  # truthy variant
    monkeypatch.setenv("TPLINK_USERNAME", "user@example.com")
    monkeypatch.setenv("TPLINK_PASSWORD", "secret")
    monkeypatch.delenv("KASA_CLOUD_MODELS", raising=False)

    result = load_cloud_client()

    assert result is not None
    client, models = result
    assert isinstance(client, KasaCloudClient)
    assert client._username == "user@example.com"
    assert models == ("HS300",)


def test_load_cloud_client_parses_custom_models(monkeypatch):
    monkeypatch.setenv("KASA_CLOUD_FALLBACK", "1")
    monkeypatch.setenv("TPLINK_USERNAME", "user@example.com")
    monkeypatch.setenv("TPLINK_PASSWORD", "secret")
    monkeypatch.setenv("KASA_CLOUD_MODELS", "HS300, KP303 ,")

    _client_obj, models = load_cloud_client()

    assert models == ("HS300", "KP303")


# ── registry integration with cloud devices ─────────────────────────────────


def _fake_cloud_device(host: str, mac: str) -> SimpleNamespace:
    return SimpleNamespace(host=host, mac=_norm_mac(mac), alias="Strip")


def test_attach_cloud_merges_into_all_and_get(monkeypatch):
    cloud = _fake_cloud_device("AA:BB:CC:DD:EE:01", "AABBCCDDEE01")
    monkeypatch.setattr(
        kasa_service, "discover_cloud_devices", AsyncMock(return_value=[cloud])
    )
    reg = DeviceRegistry(cloud_client=SimpleNamespace(), cloud_models=("HS300",))

    attached = asyncio.run(reg.attach_cloud())

    assert attached == [cloud]
    assert cloud in reg.all()
    assert reg.get("AA:BB:CC:DD:EE:01") is cloud


def test_attach_cloud_noop_when_disabled():
    reg = DeviceRegistry()  # no cloud client
    assert asyncio.run(reg.attach_cloud()) == []


def test_attach_cloud_excludes_local_macs(monkeypatch):
    spy = AsyncMock(return_value=[])
    monkeypatch.setattr(kasa_service, "discover_cloud_devices", spy)
    reg = DeviceRegistry(cloud_client=SimpleNamespace(), cloud_models=("HS300",))
    reg._devices = {
        "10.0.0.1": SimpleNamespace(host="10.0.0.1", mac="AA:BB:CC:DD:EE:01")
    }

    asyncio.run(reg.attach_cloud())

    assert spy.call_args.kwargs["skip_macs"] == frozenset({"AABBCCDDEE01"})


def test_get_usage_branches_to_cloud_energy_summary():
    reg = DeviceRegistry()
    summary = {
        "current_power_w": 10.0,
        "voltage": 120.0,
        "today_kwh": 0.2,
        "month_kwh": 4.0,
        "daily_raw": {5: 0.1, 6: 0.2},
        "monthly_raw": {3: 4.0},
    }
    reg._cloud_devices = {
        "h": SimpleNamespace(host="h", energy_summary=AsyncMock(return_value=summary))
    }

    usage = asyncio.run(reg.get_usage("h"))

    assert usage.device_id == "h"
    assert usage.current_power_w == 10.0
    assert usage.today_kwh == 0.2
    assert usage.month_kwh == 4.0
    assert [s.label for s in usage.daily] == ["5", "6"]
    assert [s.label for s in usage.monthly] == ["Mar"]


def test_build_usage_labels_and_rounds_history():
    usage = _build_usage(
        "dev",
        current_power_w=1.0,
        today_kwh=0.5,
        month_kwh=2.0,
        voltage=120.0,
        daily_raw={2: 0.123456, 1: 0.1},
        monthly_raw={1: 1.0, 6: 4.2},
    )

    assert usage.device_id == "dev"
    assert [s.label for s in usage.daily] == ["1", "2"]
    assert usage.daily[1].kwh == 0.123  # rounded to 3 dp
    assert [s.label for s in usage.monthly] == ["Jan", "Jun"]
