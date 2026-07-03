"""Tests for the crash-safe write helper shared by the JSON stores."""

import os

import pytest

from api import fsutil
from api.fsutil import atomic_write_text


def test_writes_content_and_creates_parents(tmp_path):
    target = tmp_path / "nested" / "doc.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'


def test_replaces_existing_content(tmp_path):
    target = tmp_path / "doc.json"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_leaves_no_temp_files_behind(tmp_path):
    target = tmp_path / "doc.json"
    atomic_write_text(target, "x")
    assert os.listdir(tmp_path) == ["doc.json"]


def test_failed_swap_preserves_the_old_file(tmp_path, monkeypatch):
    """The whole point: an interrupted write must never tear the target."""
    target = tmp_path / "doc.json"
    target.write_text("precious")

    def boom(src, dst):
        raise OSError("simulated crash mid-swap")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "half-written")
    assert target.read_text() == "precious"
    assert os.listdir(tmp_path) == ["doc.json"]  # temp file cleaned up
