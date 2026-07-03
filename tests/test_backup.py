"""Tests for the backup/restore endpoints.

Every store the route touches is monkeypatched to a tmp-path instance (the
pattern used by test_scenes.py/test_alerts.py), so this suite never touches the
repo's real ``data/`` directory. ``registry`` is a plain ``DeviceRegistry`` with
real ``HostStore``/``DeviceSnapshotStore`` instances so known-device export and
restore are exercised end to end.
"""

import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.alerts import AlertThresholdStore
from api.device_store import DeviceSnapshotStore, HostStore
from api.energy_history import EnergyHistoryStore
from api.group_store import GroupStore
from api.kasa_service import DeviceRegistry
from api.routers import backup as backup_routes
from api.scene_store import SceneStore
from api.schedule_store import ScheduleStore
from api.schemas import CURRENT_BACKUP_VERSION


@pytest.fixture
def stores(tmp_path):
    return {
        "groups": GroupStore(tmp_path / "groups.json"),
        "scenes": SceneStore(tmp_path / "scenes.json"),
        "schedules": ScheduleStore(tmp_path / "schedules.json"),
        "alert_thresholds": AlertThresholdStore(tmp_path / "alerts.json"),
        "history": EnergyHistoryStore(tmp_path / "energy.db"),
    }


@pytest.fixture
def registry(tmp_path):
    return DeviceRegistry(
        HostStore(tmp_path / "hosts.json"),
        snapshot_store=DeviceSnapshotStore(tmp_path / "snapshots.json"),
    )


@pytest.fixture
def client(monkeypatch, stores, registry):
    monkeypatch.setattr(backup_routes, "groups", stores["groups"])
    monkeypatch.setattr(backup_routes, "scenes", stores["scenes"])
    monkeypatch.setattr(backup_routes, "schedules", stores["schedules"])
    monkeypatch.setattr(backup_routes, "alert_thresholds", stores["alert_thresholds"])
    monkeypatch.setattr(backup_routes, "history", stores["history"])
    monkeypatch.setattr(backup_routes, "registry", registry)
    monkeypatch.setattr(backup_routes.broadcaster, "publish_now", AsyncMock())
    app = FastAPI()
    app.include_router(backup_routes.router)
    return TestClient(app)


def _seed(stores, registry):
    """Populate every store with one row so a backup has something to carry."""
    g = stores["groups"].create_group("Living Room")
    stores["groups"].update_group(g["id"], device_ids=["10.0.0.1"])
    stores["groups"].set_favorites(["10.0.0.1"])
    stores["scenes"].create_scene(
        "Movie night", [{"device_id": "10.0.0.1", "state": {"on": False}}]
    )
    stores["schedules"].create_rule(
        {
            "kind": "fixed_time",
            "enabled": True,
            "time": "18:30",
            "days": [0, 1],
            "offset_minutes": 0,
            "at": None,
            "target": {"type": "device", "id": "10.0.0.1"},
            "action": "on",
            "scene_id": None,
        }
    )
    stores["alert_thresholds"].set_all({"10.0.0.1": 42.0})
    # _store_device (not a plain dict assignment) is what populates the identity
    # snapshot registry.known_devices_export() reads back.
    asyncio.run(registry._store_device(FakeDevice("10.0.0.1", alias="Plug")))
    registry._persist()  # writes the host store + snapshot store to disk


# ── GET /api/backup ──────────────────────────────────────────────────────────


def test_backup_document_shape_and_version(client):
    body = client.get("/api/backup").json()
    assert body["backup_version"] == CURRENT_BACKUP_VERSION
    assert body["app_version"]
    assert body["created_at"]
    assert body["groups"] == []
    assert body["known_devices"] == []


def test_backup_download_has_attachment_header(client):
    r = client.get("/api/backup")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "kasa-nice-backup.json" in r.headers["content-disposition"]


def test_backup_includes_every_store(client, stores, registry):
    _seed(stores, registry)
    body = client.get("/api/backup").json()

    assert [g["name"] for g in body["groups"]] == ["Living Room"]
    assert body["favorites"] == ["10.0.0.1"]
    assert [s["name"] for s in body["scenes"]] == ["Movie night"]
    assert len(body["schedules"]) == 1
    assert body["alert_thresholds"] == {"10.0.0.1": 42.0}
    assert [d["host"] for d in body["known_devices"]] == ["10.0.0.1"]
    assert body["known_devices"][0]["snapshot"]["alias"] == "Plug"


def test_backup_skips_invalid_stored_row(client, stores):
    # A hand-corrupted row must degrade (dropped + warned), not 500 the backup.
    stores["scenes"]._write({"scenes": [{"id": "x"}]})  # missing required "name"
    body = client.get("/api/backup").json()
    assert body["scenes"] == []


# ── POST /api/backup/restore ────────────────────────────────────────────────


def _minimal_doc(**overrides):
    import datetime

    base = {
        "backup_version": CURRENT_BACKUP_VERSION,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "app_version": "0.0.0",
        "groups": [],
        "favorites": [],
        "scenes": [],
        "schedules": [],
        "alert_thresholds": {},
        "known_devices": [],
    }
    return {**base, **overrides}


def test_restore_round_trips_every_store(client, stores, registry):
    _seed(stores, registry)
    doc = client.get("/api/backup").json()

    # Wipe every store, then restore from the downloaded document.
    stores["groups"].replace_all([], [])
    stores["scenes"].replace_all([])
    stores["schedules"].replace_all([])
    stores["alert_thresholds"].set_all({})
    registry.restore_known_devices([], {})
    assert client.get("/api/backup").json()["groups"] == []

    r = client.post("/api/backup/restore", json=doc)
    assert r.status_code == 200

    restored = client.get("/api/backup").json()
    assert restored["groups"][0]["name"] == "Living Room"
    assert restored["favorites"] == ["10.0.0.1"]
    assert restored["scenes"][0]["name"] == "Movie night"
    assert len(restored["schedules"]) == 1
    assert restored["alert_thresholds"] == {"10.0.0.1": 42.0}
    assert restored["known_devices"][0]["host"] == "10.0.0.1"


def test_restore_nudges_broadcaster(client, stores, registry):
    doc = client.get("/api/backup").json()
    client.post("/api/backup/restore", json=doc)
    backup_routes.broadcaster.publish_now.assert_awaited_once()


def test_restore_rejects_unknown_backup_version(client):
    r = client.post("/api/backup/restore", json=_minimal_doc(backup_version=999))
    assert r.status_code == 422
    assert "backup_version" in r.text


def test_restore_rejects_missing_required_field(client):
    doc = _minimal_doc()
    del doc["app_version"]
    r = client.post("/api/backup/restore", json=doc)
    assert r.status_code == 422


def test_restore_rejects_malformed_group(client):
    doc = _minimal_doc(groups=[{"id": "g1"}])  # missing required "name"
    r = client.post("/api/backup/restore", json=doc)
    assert r.status_code == 422


def test_restore_rejects_bad_known_device_snapshot(client, stores):
    doc = _minimal_doc(
        known_devices=[{"host": "10.0.0.5", "snapshot": {"not": "a device"}}]
    )
    r = client.post("/api/backup/restore", json=doc)
    assert r.status_code == 422
    assert "10.0.0.5" in r.text
    # No partial write: nothing else in the payload was persisted either.
    assert stores["groups"].list_groups() == []


def test_restore_bad_payload_leaves_stores_untouched(client, stores):
    _seed_alert_only = stores["alert_thresholds"].set_all({"10.0.0.9": 5.0})
    doc = _minimal_doc(backup_version=999)
    client.post("/api/backup/restore", json=doc)
    # The pre-existing threshold must survive a rejected restore untouched.
    assert stores["alert_thresholds"].get_all() == _seed_alert_only


# ── GET /api/backup/energy.db ───────────────────────────────────────────────


def test_energy_db_missing_file_returns_404(client):
    r = client.get("/api/backup/energy.db")
    assert r.status_code == 404


def test_energy_db_streams_a_valid_sqlite_file(client, stores, tmp_path):
    stores["history"].record("10.0.0.1", 42.0, 1.5)

    r = client.get("/api/backup/energy.db")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.sqlite3"
    assert "attachment" in r.headers["content-disposition"]

    out = tmp_path / "downloaded.db"
    out.write_bytes(r.content)
    conn = sqlite3.connect(out)
    rows = conn.execute("SELECT device_id, power_w, today_kwh FROM samples").fetchall()
    assert rows == [("10.0.0.1", 42.0, 1.5)]


def test_energy_db_snapshot_is_independent_of_live_file(client, stores, tmp_path):
    # The streamed copy must reflect a point-in-time snapshot, not a handle to
    # the live (possibly still-being-written) file.
    stores["history"].record("d1", 1.0, 0.1)
    r = client.get("/api/backup/energy.db")
    stores["history"].record("d2", 2.0, 0.2)  # written after the snapshot

    out = tmp_path / "downloaded.db"
    out.write_bytes(r.content)
    conn = sqlite3.connect(out)
    rows = conn.execute("SELECT device_id FROM samples").fetchall()
    assert rows == [("d1",)]
