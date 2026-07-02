"""Tests for the TP-Link cloud control path (``api.cloud_service``).

Only the HTTP boundary is mocked: ``KasaCloudClient._raw_post`` (the sole method
that talks to the network) via ``AsyncMock``, and ``CloudDevice`` through a fake
client. Uses ``asyncio.run`` like ``test_kasa_service.py``.
"""

import asyncio
import json
import logging
from datetime import datetime
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
from api.config import Settings
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


def _clock_route(clock: dict):
    """Route emeter passthroughs, serving ``clock`` for the time read."""

    def route(_app_server_url, _device_id, request):
        if "time" in request:
            return {"time": {"get_time": clock}}
        emeter = request["emeter"]
        if "get_realtime" in emeter:
            return {
                "emeter": {"get_realtime": {"power_mw": 1000, "voltage_mv": 120000}}
            }
        if "get_daystat" in emeter:
            return {"emeter": {"get_daystat": {"day_list": []}}}
        if "get_monthstat" in emeter:
            return {"emeter": {"get_monthstat": {"month_list": []}}}
        raise AssertionError(f"unexpected request {request}")

    return route


def test_energy_summary_warns_once_on_clock_drift(caplog):
    drifted = {"year": 2025, "month": 1, "mday": 1, "hour": 0, "min": 0, "sec": 0}
    device = _cloud_device(AsyncMock(side_effect=_clock_route(drifted)))
    device.children = [CloudChild(device, "c0", "Outlet 1", True)]

    with caplog.at_level(logging.WARNING, logger="api.cloud_service"):
        asyncio.run(device.energy_summary())
        asyncio.run(device.energy_summary())

    drift = [r for r in caplog.records if "clock is off" in r.getMessage()]
    assert len(drift) == 1  # once-guard: warned only on the first poll


def test_energy_summary_no_drift_warning_when_clock_current(caplog):
    now = datetime.now()
    current = {
        "year": now.year,
        "month": now.month,
        "mday": now.day,
        "hour": now.hour,
        "min": now.minute,
        "sec": now.second,
    }
    device = _cloud_device(AsyncMock(side_effect=_clock_route(current)))
    device.children = [CloudChild(device, "c0", "Outlet 1", True)]

    with caplog.at_level(logging.WARNING, logger="api.cloud_service"):
        asyncio.run(device.energy_summary())

    assert not [r for r in caplog.records if "clock is off" in r.getMessage()]


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


# ── load_cloud_client (config-driven) ────────────────────────────────────────
#
# Build an isolated Settings (``_env_file=None``, no dotenv) and pass it in, so
# no env mutation leaks and a developer's real .env can't influence them.


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_load_cloud_client_disabled_by_default():
    assert load_cloud_client(_settings()) is None


def test_load_cloud_client_enabled_without_credentials_returns_none():
    assert load_cloud_client(_settings(kasa_cloud_fallback="1")) is None


def test_load_cloud_client_builds_client_and_default_models():
    settings = _settings(
        kasa_cloud_fallback="yes",  # truthy variant
        tplink_username="user@example.com",
        tplink_password="secret",
    )

    result = load_cloud_client(settings)

    assert result is not None
    client, models = result
    assert isinstance(client, KasaCloudClient)
    assert client._username == "user@example.com"
    assert models == ("HS300",)


def test_load_cloud_client_parses_custom_models():
    settings = _settings(
        kasa_cloud_fallback="1",
        tplink_username="user@example.com",
        tplink_password="secret",
        kasa_cloud_models="HS300, KP303 ,",
    )

    _client_obj, models = load_cloud_client(settings)

    assert models == ("HS300", "KP303")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_cloud_fallback_truthy_strings_enable(value):
    settings = _settings(
        kasa_cloud_fallback=value,
        tplink_username="user@example.com",
        tplink_password="secret",
    )
    assert load_cloud_client(settings) is not None


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "banana"])
def test_cloud_fallback_non_truthy_strings_disable(value):
    # Anything outside the historical truthy set is off — never an error.
    settings = _settings(
        kasa_cloud_fallback=value,
        tplink_username="user@example.com",
        tplink_password="secret",
    )
    assert load_cloud_client(settings) is None


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
    # Keyed by the stable (normalized-MAC) id, not the host, matching local devices.
    assert reg.get("AABBCCDDEE01") is cloud


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


def test_read_energy_snapshot_cloud_returns_summary_scalars():
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

    snapshot = asyncio.run(reg.read_energy_snapshot("h"))

    assert snapshot.power_w == 10.0
    assert snapshot.today_kwh == 0.2
    assert snapshot.month_kwh == 4.0


def test_read_energy_snapshot_cloud_round_trip_count_matches_get_usage():
    # Invariant: the recorder's snapshot read of a cloud strip must cost exactly
    # the same number of cloud round-trips as get_usage did (energy_summary()
    # already batches the per-outlet fan-out; there's no cheaper scalar read).
    clock = {"year": 2026, "month": 3, "mday": 6, "hour": 10, "min": 0, "sec": 0}

    def build_registry() -> tuple[DeviceRegistry, AsyncMock]:
        passthrough = AsyncMock(side_effect=_clock_route(clock))
        device = _cloud_device(passthrough)
        device.children = [
            CloudChild(device, "c0", "Outlet 1", True),
            CloudChild(device, "c1", "Outlet 2", False),
        ]
        reg = DeviceRegistry()
        reg._cloud_devices = {device.host: device}
        return reg, passthrough

    usage_reg, usage_passthrough = build_registry()
    asyncio.run(usage_reg.get_usage(next(iter(usage_reg._cloud_devices))))

    snap_reg, snap_passthrough = build_registry()
    asyncio.run(snap_reg.read_energy_snapshot(next(iter(snap_reg._cloud_devices))))

    # One round-trip == one passthrough. Identical count, and non-trivial.
    assert snap_passthrough.await_count == usage_passthrough.await_count
    assert snap_passthrough.await_count > 0


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
