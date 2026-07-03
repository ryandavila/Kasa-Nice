"""Tests for the shared SSE broadcaster.

Exercise the broadcaster directly with a fake registry (no hardware, no
network), matching the rest of the suite's ``asyncio.run`` style.
"""

import asyncio
import contextlib

import pytest
from conftest import FakeDevice

from api import events


class FakeRegistry:
    """Minimal stand-in exposing just what the broadcaster reads."""

    def __init__(self, devices: list[FakeDevice]):
        self._devices = devices
        self.refresh_count = 0

    def all(self) -> list[FakeDevice]:
        return list(self._devices)

    async def refresh_all(self) -> list[FakeDevice]:
        self.refresh_count += 1
        return self.all()

    def is_reachable(self, device: FakeDevice) -> bool:
        # This fake serves only live devices with no miss tracking.
        return True

    def unreachable_devices(self) -> list:
        # The broadcaster appends these to every frame; this fake serves only live
        # devices, so there are none.
        return []


@pytest.fixture
def fake_registry(monkeypatch):
    reg = FakeRegistry([FakeDevice("10.0.0.1", alias="Plug", is_on=False)])
    monkeypatch.setattr(events, "registry", reg)
    # A tiny interval keeps the loop-lifecycle tests fast.
    monkeypatch.setattr(events, "_STREAM_INTERVAL", 0.01)
    return reg


def _data_frames(queue: asyncio.Queue[str]) -> list[str]:
    """Drain a queue into a list without blocking."""
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_publish_now_fans_out_to_every_subscriber(fake_registry):
    async def scenario():
        b = events._Broadcaster()
        q1 = b.subscribe()
        q2 = b.subscribe()
        await b.publish_now()
        return _data_frames(q1), _data_frames(q2)

    f1, f2 = asyncio.run(scenario())
    assert len(f1) == 1 and len(f2) == 1
    assert f1[0] == f2[0]
    assert f1[0].startswith("data: ")


def test_refresh_loop_starts_with_first_subscriber_and_stops_with_last(fake_registry):
    async def scenario():
        b = events._Broadcaster()
        assert b._task is None  # idle: nothing polling hardware
        q = b.subscribe()
        assert b._task is not None
        # Let the shared loop tick at least once.
        await asyncio.sleep(0.05)
        started_refreshes = fake_registry.refresh_count
        b.unsubscribe(q)
        assert b._task is None
        # No further hardware reads once the last subscriber leaves.
        await asyncio.sleep(0.05)
        return started_refreshes, fake_registry.refresh_count

    started, after_stop = asyncio.run(scenario())
    assert started >= 1
    assert after_stop == started


def test_change_suppression_sends_keepalive_when_unchanged(fake_registry):
    async def scenario():
        b = events._Broadcaster()
        q = b.subscribe()
        await b.publish_now()  # first frame: full data
        await b.publish_now()  # identical state: keepalive only
        # A real change re-sends data.
        fake_registry._devices[0].is_on = True
        await b.publish_now()
        return _data_frames(q)

    frames = asyncio.run(scenario())
    assert len(frames) == 3
    assert frames[0].startswith("data: ")
    assert frames[1] == events._KEEPALIVE
    assert frames[2].startswith("data: ")
    assert frames[2] != frames[0]  # reflects the toggled state


def test_publish_now_is_noop_without_subscribers(fake_registry):
    async def scenario():
        b = events._Broadcaster()
        # Nobody subscribed: nothing to do, and the loop isn't running.
        await b.publish_now()
        return b._last_frame

    assert asyncio.run(scenario()) is None


def test_loop_emits_keepalive_when_refresh_fails(fake_registry, monkeypatch):
    async def boom():
        raise RuntimeError("transient read error")

    monkeypatch.setattr(fake_registry, "refresh_all", boom)

    async def scenario():
        b = events._Broadcaster()
        q = b.subscribe()
        await asyncio.sleep(0.05)  # let the loop tick and fail
        b.unsubscribe(q)
        return _data_frames(q)

    frames = asyncio.run(scenario())
    assert frames  # got at least one
    assert all(f == events._KEEPALIVE for f in frames)


def test_loop_survives_a_serialization_failure(fake_registry, monkeypatch):
    """A device raising while being serialized must not kill the shared loop.

    Regression: the frame build used to sit outside the loop's try/except, so
    one bad ``serialize_device`` ended live updates for every client until a
    server restart.
    """
    real = events.serialize_device
    failures = iter([True])  # fail exactly the first frame, then recover

    def flaky(device, **kwargs):
        if next(failures, False):
            raise RuntimeError("partially updated device")
        return real(device, **kwargs)

    monkeypatch.setattr(events, "serialize_device", flaky)

    async def scenario():
        b = events._Broadcaster()
        q = b.subscribe()
        await asyncio.sleep(0.08)  # several ticks: one bad frame, then good ones
        assert not b._task.done()  # the loop is still alive
        b.unsubscribe(q)
        return _data_frames(q)

    frames = asyncio.run(scenario())
    assert any(f == events._KEEPALIVE for f in frames)  # the failed tick
    assert any(f.startswith("data: ") for f in frames)  # recovered after it


def test_subscribe_restarts_a_dead_loop(fake_registry):
    """A crashed (done but non-None) task is replaced on the next subscribe."""

    async def scenario():
        b = events._Broadcaster()
        q1 = b.subscribe()
        dead = b._task
        dead.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dead
        q2 = b.subscribe()
        assert b._task is not dead and not b._task.done()
        await asyncio.sleep(0.05)  # the new loop ticks and serves frames
        b.unsubscribe(q1)
        b.unsubscribe(q2)
        return _data_frames(q2)

    assert asyncio.run(scenario())
