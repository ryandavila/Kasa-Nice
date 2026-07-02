import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from api.schedule_store import ScheduleStore


@pytest.fixture
def store(tmp_path):
    return ScheduleStore(tmp_path / "schedules.json")


def _rule(**over) -> dict:
    """A minimal valid stored rule, with per-test overrides."""
    base = {
        "kind": "fixed_time",
        "enabled": True,
        "time": "18:30",
        "days": [0, 1, 2],
        "target": {"type": "device", "id": "10.0.0.1"},
        "action": "on",
    }
    return {**base, **over}


# ── Store ───────────────────────────────────────────────────────────────────


def test_missing_file_reads_empty(store):
    assert store.list_rules() == []


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = tmp_path / "schedules.json"
    path.write_text("{ not json")
    assert ScheduleStore(path).list_rules() == []


def test_create_assigns_id_and_null_last_fired(store):
    created = store.create_rule(_rule())
    assert created["id"]
    assert created["last_fired"] is None
    assert created["time"] == "18:30"
    assert [r["id"] for r in store.list_rules()] == [created["id"]]


def test_create_ignores_client_supplied_id(store):
    # The server owns the id, so a spoofed one in the payload is overwritten.
    created = store.create_rule(_rule(id="hacker"))
    assert created["id"] != "hacker"


def test_update_merges_only_given_fields(store):
    created = store.create_rule(_rule())
    updated = store.update_rule(created["id"], {"enabled": False})
    assert updated["enabled"] is False
    assert updated["time"] == "18:30"  # untouched
    assert updated["days"] == [0, 1, 2]


def test_update_cannot_rekey_via_id_field(store):
    created = store.create_rule(_rule())
    store.update_rule(created["id"], {"id": "other", "action": "off"})
    assert store.get_rule(created["id"])["action"] == "off"
    assert store.get_rule("other") is None


def test_update_unknown_returns_none(store):
    assert store.update_rule("nope", {"enabled": False}) is None


def test_delete_rule(store):
    created = store.create_rule(_rule())
    assert store.delete_rule(created["id"]) is True
    assert store.list_rules() == []
    assert store.delete_rule(created["id"]) is False


def test_mark_fired_records_ts_and_result(store):
    created = store.create_rule(_rule())
    store.mark_fired(created["id"], 1700000000, "ok")
    assert store.get_rule(created["id"])["last_fired"] == {
        "ts": 1700000000,
        "result": "ok",
    }


def test_rules_persist_across_instances(tmp_path):
    path = tmp_path / "schedules.json"
    ScheduleStore(path).create_rule(_rule())
    assert len(ScheduleStore(path).list_rules()) == 1


# ── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, store):
    monkeypatch.setattr(routes, "schedules", store)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _payload(**over) -> dict:
    base = {
        "time": "07:15",
        "days": [1, 3, 5],
        "target": {"type": "device", "id": "10.0.0.1"},
        "action": "on",
    }
    return {**base, **over}


def test_api_schedule_crud_round_trip(client):
    assert client.get("/api/schedules").json() == []

    created = client.post("/api/schedules", json=_payload())
    assert created.status_code == 201
    body = created.json()
    sid = body["id"]
    assert body["kind"] == "fixed_time"
    assert body["enabled"] is True
    assert body["last_fired"] is None

    patched = client.patch(f"/api/schedules/{sid}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["time"] == "07:15"  # partial update left it alone

    assert client.get("/api/schedules").json()[0]["enabled"] is False

    assert client.delete(f"/api/schedules/{sid}").status_code == 204
    assert client.get("/api/schedules").json() == []


def test_api_create_normalizes_days(client):
    # Out-of-order duplicates are de-duped and sorted by the validator.
    body = client.post("/api/schedules", json=_payload(days=[5, 1, 5, 3])).json()
    assert body["days"] == [1, 3, 5]


def test_api_create_room_target(client):
    body = client.post(
        "/api/schedules",
        json=_payload(target={"type": "room", "id": "room-1"}, action="off"),
    ).json()
    assert body["target"] == {"type": "room", "id": "room-1"}
    assert body["action"] == "off"


@pytest.mark.parametrize("bad_time", ["7:15", "25:00", "07:60", "0715", "noon"])
def test_api_create_rejects_bad_time(client, bad_time):
    assert (
        client.post("/api/schedules", json=_payload(time=bad_time)).status_code == 422
    )


def test_api_create_rejects_bad_days(client):
    assert client.post("/api/schedules", json=_payload(days=[7])).status_code == 422
    assert client.post("/api/schedules", json=_payload(days=[])).status_code == 422


def test_api_create_rejects_bad_target_and_action(client):
    assert (
        client.post(
            "/api/schedules", json=_payload(target={"type": "zone", "id": "x"})
        ).status_code
        == 422
    )
    assert client.post("/api/schedules", json=_payload(action="dim")).status_code == 422


def test_api_patch_unknown_404(client):
    assert (
        client.patch("/api/schedules/nope", json={"enabled": False}).status_code == 404
    )


def test_api_delete_unknown_404(client):
    assert client.delete("/api/schedules/nope").status_code == 404
