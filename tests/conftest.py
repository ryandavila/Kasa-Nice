"""Shared test doubles for the Kasa-Nice backend.

The fakes themselves live in :mod:`api.testing.fake_devices` so they can be
shared with the ``KASA_FAKE_DEVICES`` runtime seam (which seeds the live
registry for the browser end-to-end test) without duplicating them. This module
re-exports them so existing tests keep importing ``from conftest import ...``.
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
    """Keep configuration hermetic across the whole suite.

    A developer's real repo-root ``.env`` must never change test outcomes, so we
    seed a settings instance built from the process environment only
    (``_env_file=None``) and drop it afterwards. Any code that reaches for
    ``get_settings()`` during a test therefore sees a clean, dotenv-free slate;
    tests that exercise env parsing build their own ``Settings`` and pass it in.
    """
    config.set_settings(config.Settings(_env_file=None))
    yield
    config.reset_settings()
