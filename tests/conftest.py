"""Shared test doubles for the Kasa-Nice backend.

The fakes live in :mod:`api.testing.fake_devices` so the ``KASA_FAKE_DEVICES``
seam can share them; this module re-exports them so tests keep importing
``from conftest import ...``.
"""

import pytest

from api import config
from api.testing.fake_devices import (
    FakeChild,
    FakeDevice,
    FakeDeviceType,
    FakeDiscover,
    FakeEnergy,
    FakeLight,
)

__all__ = [
    "FakeChild",
    "FakeDevice",
    "FakeDeviceType",
    "FakeDiscover",
    "FakeEnergy",
    "FakeLight",
]


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Keep configuration hermetic across the suite.

    Seed a settings instance built from the environment only (``_env_file=None``)
    so a developer's repo-root ``.env`` can't change test outcomes, and drop it
    afterwards. Tests exercising env parsing build their own ``Settings``.
    """
    config.set_settings(config.Settings(_env_file=None))
    yield
    config.reset_settings()
