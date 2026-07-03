import asyncio
from unittest.mock import AsyncMock

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import scene_service
from api.kasa_service import DeviceRegistry
from api.routers import scenes as scenes_routes
from api.scene_store import SceneStore


@pytest.fixture
def store(tmp_path):
    return SceneStore(tmp_path / "scenes.json")


# ── Store ───────────────────────────────────────────────────────────────────


def test_missing_file_reads_empty(store):
    assert store.list_scenes() == []


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = tmp_path / "scenes.json"
    path.write_text("{ this is not json")
    assert SceneStore(path).list_scenes() == []


def test_create_and_list_scenes(store):
    s = store.create_scene("Movie night", [{"device_id": "d1", "state": {"on": False}}])
    assert s["name"] == "Movie night"
    assert s["entries"] == [{"device_id": "d1", "state": {"on": False}}]
    assert s["id"]
    assert [x["id"] for x in store.list_scenes()] == [s["id"]]


def test_update_scene_name_only_keeps_entries(store):
    entries = [{"device_id": "d1", "state": {"on": True}}]
    s = store.create_scene("Evening", entries)
    updated = store.update_scene(s["id"], name="Night")
    assert updated["name"] == "Night"
    assert updated["entries"] == entries


def test_update_scene_entries_only_keeps_name(store):
    s = store.create_scene("Evening", [{"device_id": "d1", "state": {"on": True}}])
    new_entries = [{"device_id": "d2", "state": {"on": False}}]
    updated = store.update_scene(s["id"], entries=new_entries)
    assert updated["name"] == "Evening"
    assert updated["entries"] == new_entries


def test_update_unknown_scene_returns_none(store):
    assert store.update_scene("nope", name="x") is None


def test_delete_scene(store):
    s = store.create_scene("Bright", [])
    assert store.delete_scene(s["id"]) is True
    assert store.list_scenes() == []
    assert store.delete_scene(s["id"]) is False


def test_scenes_persist_across_instances(tmp_path):
    path = tmp_path / "scenes.json"
    SceneStore(path).create_scene(
        "Reading", [{"device_id": "d1", "state": {"on": True}}]
    )
    assert [s["name"] for s in SceneStore(path).list_scenes()] == ["Reading"]


# ── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry():
    """A registry with a plain plug and a colour+dimmable bulb (both off)."""
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Plug"),
        "10.0.0.2": FakeDevice(
            "10.0.0.2", alias="Bulb", is_color=True, is_dimmable=True
        ),
    }
    return reg


@pytest.fixture
def client(monkeypatch, store, registry):
    # Wire the same store/registry into both the route module (list/create/
    # snapshot/patch/delete) and the service module (apply), so they agree.
    monkeypatch.setattr(scenes_routes, "registry", registry)
    monkeypatch.setattr(scenes_routes, "scenes", store)
    monkeypatch.setattr(scene_service, "registry", registry)
    monkeypatch.setattr(scene_service, "scenes", store)
    app = FastAPI()
    app.include_router(scenes_routes.router)
    return TestClient(app)


def test_create_scene_with_explicit_entries(client):
    r = client.post(
        "/api/scenes",
        json={
            "name": "Focus",
            "entries": [
                {"device_id": "10.0.0.2", "state": {"on": True, "brightness": 80}}
            ],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Focus"
    assert body["entries"][0]["state"]["brightness"] == 80


def test_create_scene_from_device_ids_snapshots_current_state(client):
    # The bulb is a colour+dimmable light; snapshotting captures its live
    # brightness/hsv (FakeLight defaults) alongside on/off.
    r = client.post("/api/scenes", json={"name": "Now", "device_ids": ["10.0.0.2"]})
    assert r.status_code == 201
    entry = r.json()["entries"][0]
    assert entry["device_id"] == "10.0.0.2"
    assert entry["state"] == {"on": False, "brightness": 50, "hsv": [120, 100, 100]}


def test_create_scene_from_device_ids_plain_plug_has_no_light_fields(client):
    # A plain plug snapshots only on/off; the light fields serialize as null.
    r = client.post("/api/scenes", json={"name": "Now", "device_ids": ["10.0.0.1"]})
    state = r.json()["entries"][0]["state"]
    assert state["on"] is False
    assert state["brightness"] is None
    assert state["hsv"] is None


def test_create_scene_rejects_both_sources_422(client):
    r = client.post(
        "/api/scenes",
        json={"name": "X", "entries": [], "device_ids": ["10.0.0.1"]},
    )
    assert r.status_code == 422


def test_create_scene_rejects_neither_source_422(client):
    assert client.post("/api/scenes", json={"name": "X"}).status_code == 422


def test_scene_crud_round_trip(client):
    created = client.post("/api/scenes", json={"name": "Den", "entries": []})
    assert created.status_code == 201
    sid = created.json()["id"]

    patched = client.patch(f"/api/scenes/{sid}", json={"name": "Study"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Study"

    assert client.get("/api/scenes").json()[0]["name"] == "Study"

    assert client.delete(f"/api/scenes/{sid}").status_code == 204
    assert client.get("/api/scenes").json() == []


def test_patch_unknown_scene_404(client):
    assert client.patch("/api/scenes/nope", json={"name": "x"}).status_code == 404


def test_delete_unknown_scene_404(client):
    assert client.delete("/api/scenes/nope").status_code == 404


def test_apply_scene_sets_power_and_light(client, registry):
    r = client.post(
        "/api/scenes",
        json={
            "name": "Warm",
            "entries": [
                {"device_id": "10.0.0.1", "state": {"on": True}},
                {
                    "device_id": "10.0.0.2",
                    "state": {"on": True, "brightness": 30, "hsv": [0, 100, 100]},
                },
            ],
        },
    )
    sid = r.json()["id"]

    applied = client.post(f"/api/scenes/{sid}/apply")
    assert applied.status_code == 200
    assert applied.json() == {"succeeded": ["10.0.0.1", "10.0.0.2"], "failed": []}

    from kasa import Module

    plug = registry.get("10.0.0.1")
    bulb = registry.get("10.0.0.2")
    assert plug.is_on is True
    assert bulb.is_on is True
    light = bulb.modules[Module.Light]
    assert light.brightness == 30
    assert light.hsv == (0, 100, 100)


def test_apply_scene_skips_light_when_entry_off(client, registry):
    # An "off" entry must not push brightness/hsv (which would re-light it).
    from kasa import Module

    bulb = registry.get("10.0.0.2")
    bulb.is_on = True
    original = bulb.modules[Module.Light].brightness
    r = client.post(
        "/api/scenes",
        json={
            "name": "Off",
            "entries": [
                {"device_id": "10.0.0.2", "state": {"on": False, "brightness": 5}}
            ],
        },
    )
    sid = r.json()["id"]
    client.post(f"/api/scenes/{sid}/apply")
    assert bulb.is_on is False
    assert bulb.modules[Module.Light].brightness == original


def test_apply_scene_partial_failure_stays_200(client, registry, monkeypatch):
    # One device errors when switched; the other still applies and it's reported
    # under failed rather than as a 500.
    async def boom() -> None:
        raise RuntimeError("device offline")

    monkeypatch.setattr(registry.get("10.0.0.2"), "turn_on", boom)
    r = client.post(
        "/api/scenes",
        json={
            "name": "Both",
            "entries": [
                {"device_id": "10.0.0.1", "state": {"on": True}},
                {"device_id": "10.0.0.2", "state": {"on": True}},
            ],
        },
    )
    sid = r.json()["id"]
    applied = client.post(f"/api/scenes/{sid}/apply")
    assert applied.status_code == 200
    body = applied.json()
    assert body["succeeded"] == ["10.0.0.1"]
    assert body["failed"] == ["10.0.0.2"]


def test_apply_scene_missing_device_counts_as_failed(client):
    r = client.post(
        "/api/scenes",
        json={
            "name": "Gone",
            "entries": [{"device_id": "9.9.9.9", "state": {"on": True}}],
        },
    )
    sid = r.json()["id"]
    body = client.post(f"/api/scenes/{sid}/apply").json()
    assert body["succeeded"] == []
    assert body["failed"] == ["9.9.9.9"]


def test_apply_unknown_scene_404(client):
    assert client.post("/api/scenes/nope/apply").status_code == 404


def test_apply_scene_nudges_broadcaster(client, monkeypatch):
    # Applying a scene changes device state, so other clients must be pushed a
    # fresh frame immediately (the service does this, not the route).
    nudge = AsyncMock()
    monkeypatch.setattr(scene_service.broadcaster, "publish_now", nudge)
    r = client.post(
        "/api/scenes",
        json={
            "name": "S",
            "entries": [{"device_id": "10.0.0.1", "state": {"on": True}}],
        },
    )
    sid = r.json()["id"]
    assert client.post(f"/api/scenes/{sid}/apply").status_code == 200
    nudge.assert_awaited_once()


def test_apply_scene_service_seam_is_callable_without_http(
    store, registry, monkeypatch
):
    # Schedules will drive scenes through this function directly; assert it's
    # importable and awaitable, returning the fan-out result.
    monkeypatch.setattr(scene_service, "registry", registry)
    monkeypatch.setattr(scene_service, "scenes", store)
    monkeypatch.setattr(scene_service.broadcaster, "publish_now", AsyncMock())
    created = store.create_scene(
        "S", [{"device_id": "10.0.0.1", "state": {"on": True}}]
    )

    async def go():
        return await scene_service.apply_scene(created["id"])

    result = asyncio.run(go())
    assert result.succeeded == ["10.0.0.1"]
    assert registry.get("10.0.0.1").is_on is True
