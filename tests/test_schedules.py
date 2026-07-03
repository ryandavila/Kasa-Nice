import datetime
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from api.schedule_store import ScheduleStore
from api.schemas import Schedule


class _FakeSettings:
    """Minimal stand-in exposing only what the schedule routes read."""

    def __init__(self, location: tuple[float, float] | None) -> None:
        self.location = location


def _set_location(monkeypatch, location: tuple[float, float] | None) -> None:
    """Force the server's configured location for a test (independent of .env)."""
    monkeypatch.setattr(routes, "get_settings", lambda: _FakeSettings(location))


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


# ── New rule kinds & actions (create validation) ─────────────────────────────

NYC = (40.7128, -74.0060)


def test_api_create_sunrise_requires_location(client, monkeypatch):
    _set_location(monkeypatch, None)
    resp = client.post(
        "/api/schedules",
        json=_payload(kind="sunrise", offset_minutes=-30, time=None),
    )
    assert resp.status_code == 422
    assert "location" in resp.json()["detail"].lower()


def test_api_create_sunset_with_location_ok(client, monkeypatch):
    _set_location(monkeypatch, NYC)
    body = client.post(
        "/api/schedules",
        json=_payload(kind="sunset", offset_minutes=15, time=None),
    ).json()
    assert body["kind"] == "sunset"
    assert body["offset_minutes"] == 15
    assert body["time"] is None


def _future_at(days: int = 1) -> str:
    """A naive local 'YYYY-MM-DDTHH:MM' safely in the future."""
    return (datetime.datetime.now() + datetime.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M"
    )


def test_api_create_once_rule_shape(client, monkeypatch):
    _set_location(monkeypatch, None)  # once needs no location
    at = _future_at()
    body = client.post(
        "/api/schedules",
        json={
            "kind": "once",
            "at": at,
            "target": {"type": "device", "id": "10.0.0.1"},
            "action": "on",
        },
    ).json()
    assert body["kind"] == "once"
    assert body["at"] == at
    assert body["days"] == []


@pytest.mark.parametrize(
    "bad_at",
    [
        "not-a-datetime",
        "2030-06-01T07:15+00:00",  # offset would be silently dropped by the match
        "2030-06-01",  # date-only would silently mean midnight
    ],
)
def test_api_create_once_rejects_bad_at(client, bad_at):
    resp = client.post(
        "/api/schedules",
        json={
            "kind": "once",
            "at": bad_at,
            "target": {"type": "device", "id": "10.0.0.1"},
            "action": "on",
        },
    )
    assert resp.status_code == 422


def test_api_create_once_rejects_past_at(client):
    # A one-shot in the past could only ever be marked "missed"; reject it.
    resp = client.post(
        "/api/schedules",
        json={
            "kind": "once",
            "at": "2024-06-01T07:15",
            "target": {"type": "device", "id": "10.0.0.1"},
            "action": "on",
        },
    )
    assert resp.status_code == 422
    assert "past" in resp.json()["detail"].lower()


def test_api_patch_validates_merged_rule_before_persisting(client, store):
    """An incoherent patch must not reach the file.

    Regression: the merge was persisted before cross-field validation, so one
    bad PATCH (e.g. kind=once with no 'at') 500'd every subsequent GET until
    the JSON file was repaired by hand.
    """
    sid = client.post("/api/schedules", json=_payload()).json()["id"]

    resp = client.patch(f"/api/schedules/{sid}", json={"kind": "once"})
    assert resp.status_code == 422

    # The stored rule is untouched and the collection still serves.
    assert store.get_rule(sid)["kind"] == "fixed_time"
    listing = client.get("/api/schedules")
    assert listing.status_code == 200
    assert listing.json()[0]["kind"] == "fixed_time"


def test_api_patch_to_sun_kind_requires_location(client, monkeypatch):
    """PATCH can't sneak in what POST rejects: a sun rule with no location."""
    _set_location(monkeypatch, None)
    sid = client.post("/api/schedules", json=_payload()).json()["id"]
    resp = client.patch(f"/api/schedules/{sid}", json={"kind": "sunset", "time": None})
    assert resp.status_code == 422
    assert "location" in resp.json()["detail"].lower()


def test_api_patch_enable_toggle_skips_fireability_recheck(client, monkeypatch):
    """Toggling ``enabled`` on an old one-shot must not 422 on its past 'at'.

    The scheduler will mark it missed; the PATCH itself only touches ``enabled``.
    """
    _set_location(monkeypatch, None)
    at = _future_at()
    sid = client.post(
        "/api/schedules",
        json={
            "kind": "once",
            "at": at,
            "target": {"type": "device", "id": "10.0.0.1"},
            "action": "on",
        },
    ).json()["id"]
    assert (
        client.patch(f"/api/schedules/{sid}", json={"enabled": False}).status_code
        == 200
    )
    assert (
        client.patch(f"/api/schedules/{sid}", json={"enabled": True}).status_code == 200
    )


def test_api_create_scene_action_shape(client):
    # A scene action needs a scene_id and no target (the scene owns its devices).
    body = client.post(
        "/api/schedules",
        json={
            "kind": "fixed_time",
            "time": "20:00",
            "days": [0, 2, 4],
            "action": "scene",
            "scene_id": "movie-night",
        },
    ).json()
    assert body["action"] == "scene"
    assert body["scene_id"] == "movie-night"
    assert body["target"] is None


def test_api_create_scene_action_requires_scene_id(client):
    resp = client.post(
        "/api/schedules",
        json={"kind": "fixed_time", "time": "20:00", "days": [0], "action": "scene"},
    )
    assert resp.status_code == 422


def test_api_create_onoff_requires_target(client):
    # Omitting the target for a plain on/off action is rejected.
    resp = client.post(
        "/api/schedules",
        json={"kind": "fixed_time", "time": "20:00", "days": [0], "action": "on"},
    )
    assert resp.status_code == 422


# ── Migration: a v1-shape rules file loads and round-trips unchanged ──────────


def test_v1_shape_rule_loads_validates_and_round_trips(tmp_path):
    # A file written by v1: fixed_time rules with none of the newer fields (and one
    # even older rule with no ``kind`` at all).
    path = tmp_path / "schedules.json"
    old = {
        "id": "old1",
        "kind": "fixed_time",
        "enabled": True,
        "time": "07:00",
        "days": [0, 1, 2, 3, 4],
        "target": {"type": "device", "id": "10.0.0.1"},
        "action": "on",
        "last_fired": None,
    }
    oldest = {
        "id": "old0",
        "enabled": True,
        "time": "22:00",
        "days": [5, 6],
        "target": {"type": "room", "id": "r1"},
        "action": "off",
        "last_fired": None,
    }
    path.write_text(json.dumps({"schedules": [old, oldest]}))
    store = ScheduleStore(path)

    raw = store.list_rules()
    assert raw == [old, oldest]  # persisted dicts are untouched

    # Both validate through the current model, defaulting the new fields.
    model = Schedule(**raw[0])
    assert model.kind == "fixed_time"
    assert model.offset_minutes == 0
    assert model.at is None
    assert model.scene_id is None
    # The kind-less oldest rule loads as fixed_time.
    assert Schedule(**raw[1]).kind == "fixed_time"

    # A no-op re-read leaves the file's rules exactly as written.
    assert ScheduleStore(path).get_rule("old1") == old
