import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.group_store import GroupStore
from api.routers import groups as groups_routes


@pytest.fixture
def store(tmp_path):
    return GroupStore(tmp_path / "groups.json")


# ── Store ───────────────────────────────────────────────────────────────────


def test_missing_file_reads_empty(store):
    assert store.list_groups() == []
    assert store.get_favorites() == []


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text("{ this is not json")
    s = GroupStore(path)
    assert s.list_groups() == []
    assert s.get_favorites() == []


def test_create_and_list_groups(store):
    g = store.create_group("Living Room")
    assert g["name"] == "Living Room"
    assert g["device_ids"] == []
    assert g["id"]
    assert [x["id"] for x in store.list_groups()] == [g["id"]]


def test_update_group_name_only(store):
    g = store.create_group("Bedroom")
    updated = store.update_group(g["id"], name="Main Bedroom")
    assert updated["name"] == "Main Bedroom"
    assert updated["device_ids"] == []


def test_update_group_device_ids_dedupes(store):
    g = store.create_group("Office")
    updated = store.update_group(
        g["id"], device_ids=["10.0.0.1", "10.0.0.2", "10.0.0.1"]
    )
    assert updated["device_ids"] == ["10.0.0.1", "10.0.0.2"]


def test_update_unknown_group_returns_none(store):
    assert store.update_group("nope", name="x") is None


def test_delete_group(store):
    g = store.create_group("Hallway")
    assert store.delete_group(g["id"]) is True
    assert store.list_groups() == []
    assert store.delete_group(g["id"]) is False


def test_favorites_round_trip_and_dedupe(store):
    result = store.set_favorites(["10.0.0.5", "10.0.0.6", "10.0.0.5"])
    assert result == ["10.0.0.5", "10.0.0.6"]
    assert store.get_favorites() == ["10.0.0.5", "10.0.0.6"]


def test_groups_persist_across_instances(tmp_path):
    path = tmp_path / "groups.json"
    GroupStore(path).create_group("Kitchen")
    assert [g["name"] for g in GroupStore(path).list_groups()] == ["Kitchen"]


# ── one-time id migration (IP -> stable id) ──────────────────────────────────


def test_migrate_device_id_rewrites_groups_and_favorites(store):
    g = store.create_group("Living Room")
    store.update_group(g["id"], device_ids=["10.0.0.5", "10.0.0.6"])
    store.set_favorites(["10.0.0.5"])

    assert store.migrate_device_id("10.0.0.5", "AABBCCDDEE01") is True
    assert store.list_groups()[0]["device_ids"] == ["AABBCCDDEE01", "10.0.0.6"]
    assert store.get_favorites() == ["AABBCCDDEE01"]


def test_migrate_device_id_noop_when_old_id_absent(store):
    store.set_favorites(["10.0.0.6"])
    assert store.migrate_device_id("10.0.0.5", "AABBCCDDEE01") is False
    assert store.get_favorites() == ["10.0.0.6"]


def test_migrate_device_id_dedupes_when_both_ids_present(store):
    g = store.create_group("Room")
    store.update_group(g["id"], device_ids=["AABBCCDDEE01", "10.0.0.5"])
    store.migrate_device_id("10.0.0.5", "AABBCCDDEE01")
    assert store.list_groups()[0]["device_ids"] == ["AABBCCDDEE01"]


# ── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, store):
    monkeypatch.setattr(groups_routes, "groups", store)
    app = FastAPI()
    app.include_router(groups_routes.router)
    return TestClient(app)


def test_api_group_crud_round_trip(client):
    assert client.get("/api/groups").json() == []

    created = client.post("/api/groups", json={"name": "Den"})
    assert created.status_code == 201
    gid = created.json()["id"]

    patched = client.patch(f"/api/groups/{gid}", json={"device_ids": ["10.0.0.1"]})
    assert patched.status_code == 200
    assert patched.json()["device_ids"] == ["10.0.0.1"]

    assert client.get("/api/groups").json()[0]["device_ids"] == ["10.0.0.1"]

    assert client.delete(f"/api/groups/{gid}").status_code == 204
    assert client.get("/api/groups").json() == []


def test_api_create_group_rejects_empty_name(client):
    assert client.post("/api/groups", json={"name": ""}).status_code == 422


def test_api_patch_unknown_group_404(client):
    assert client.patch("/api/groups/nope", json={"name": "x"}).status_code == 404


def test_api_delete_unknown_group_404(client):
    assert client.delete("/api/groups/nope").status_code == 404


def test_api_favorites_round_trip(client):
    assert client.get("/api/favorites").json() == {"device_ids": []}
    put = client.put("/api/favorites", json={"device_ids": ["10.0.0.1", "10.0.0.1"]})
    assert put.status_code == 200
    assert put.json() == {"device_ids": ["10.0.0.1"]}
    assert client.get("/api/favorites").json() == {"device_ids": ["10.0.0.1"]}
