"""Contract tests for the shared ``JsonDocumentStore`` base.

Exercises the tolerant-load / atomic-warn-on-failure-save behaviour directly,
through a tiny concrete subclass, so the four real stores don't each have to
re-prove it. Their domain behaviour is covered by test_groups/scenes/schedules/
alerts.
"""

import logging

import pytest

from api.json_store import JsonDocumentStore


class _DictStore(JsonDocumentStore):
    """Minimal concrete store: a ``{"items": [...]}`` document."""

    _label = "test store"

    def _empty(self) -> dict:
        return {"items": []}

    def _coerce(self, data: object) -> dict:
        if not isinstance(data, dict):
            return self._empty()
        items = data.get("items")
        return {"items": items if isinstance(items, list) else []}


@pytest.fixture
def store(tmp_path):
    return _DictStore(tmp_path / "doc.json")


def test_missing_file_reads_empty_without_warning(store, caplog):
    with caplog.at_level(logging.WARNING):
        assert store._read() == {"items": []}
    assert caplog.records == []


def test_empty_returns_a_fresh_mutable_each_call(store):
    a = store._empty()
    a["items"].append("x")
    assert store._empty() == {"items": []}


def test_write_then_read_roundtrips(store):
    store._write({"items": [1, 2, 3]})
    assert store._read() == {"items": [1, 2, 3]}


def test_corrupt_json_warns_and_degrades_to_empty(store, caplog):
    store.path.write_text("{ not json")
    with caplog.at_level(logging.WARNING):
        assert store._read() == {"items": []}
    assert any("Could not read test store" in r.message for r in caplog.records)


def test_wrong_top_level_shape_coerces_to_empty(store):
    store.path.write_text("[1, 2, 3]")  # a list, not the expected object
    assert store._read() == {"items": []}


def test_coerce_runs_on_every_read(store):
    # Right key, wrong value type -> normalised to empty list on load.
    store.path.write_text('{"items": "nope"}')
    assert store._read() == {"items": []}


def test_write_failure_warns_and_is_swallowed(tmp_path, caplog):
    # A path whose parent can't be created (a file stands where a dir is needed)
    # makes atomic_write_text raise OSError, which _write must log and swallow.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    store = _DictStore(blocker / "doc.json")
    with caplog.at_level(logging.WARNING):
        store._write({"items": [1]})  # must not raise
    assert any("Could not write test store" in r.message for r in caplog.records)
