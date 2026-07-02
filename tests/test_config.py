"""Tests for the consolidated pydantic-settings configuration (``api.config``).

Every ``Settings`` here is built with ``_env_file=None`` so the suite reads only
what each test passes — never a developer's real repo-root ``.env`` — and no
environment mutation leaks between tests.
"""

import logging
from pathlib import Path

import pytest

from api.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ── defaults when nothing is set ─────────────────────────────────────────────


def test_defaults_match_readme(monkeypatch):
    # Clear anything the ambient environment might set so we see pure defaults.
    for var in (
        "KASA_HOST",
        "KASA_PORT",
        "KASA_STATE_FILE",
        "KASA_GROUPS_FILE",
        "KASA_ENERGY_HISTORY_FILE",
        "KASA_ENERGY_SAMPLE_INTERVAL",
        "KASA_ENERGY_RATE",
        "KASA_ENERGY_CURRENCY",
        "KASA_CLOUD_FALLBACK",
        "KASA_CLOUD_MODELS",
        "KASA_CLOUD_POLL_INTERVAL",
        "KASA_SCAN_SUBNET",
        "TPLINK_USERNAME",
        "TPLINK_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)

    s = _settings()

    assert s.kasa_host == "127.0.0.1"
    assert s.kasa_port == 8080
    assert s.kasa_state_file == Path("data/known_devices.json")
    assert s.kasa_snapshot_file == Path("data/device_snapshots.json")
    assert s.kasa_groups_file == Path("data/groups.json")
    assert s.kasa_energy_history_file == Path("data/energy_history.db")
    assert s.kasa_energy_sample_interval == 300.0
    assert s.kasa_energy_rate is None
    assert s.kasa_energy_currency == "$"
    assert s.kasa_cloud_fallback is False
    assert s.cloud_models == ("HS300",)
    assert s.kasa_cloud_poll_interval == 30.0
    assert s.kasa_scan_subnet is None
    assert s.tplink_username is None
    assert s.tplink_password is None
    # Cloud RPC identity defaults (not in the README table).
    assert s.kasa_cloud_app_type == "Tapo_Android"
    assert s.kasa_cloud_app_version == "2.8.14"
    assert s.kasa_cloud_terminal_uuid is None


# ── environment overrides ────────────────────────────────────────────────────


def test_env_overrides_are_read(monkeypatch):
    monkeypatch.setenv("KASA_HOST", "0.0.0.0")
    monkeypatch.setenv("KASA_PORT", "9000")
    monkeypatch.setenv("KASA_ENERGY_RATE", "0.18")
    monkeypatch.setenv("KASA_ENERGY_CURRENCY", "€")
    monkeypatch.setenv("KASA_STATE_FILE", "/data/known.json")

    s = _settings()

    assert s.kasa_host == "0.0.0.0"
    assert s.kasa_port == 9000
    assert s.kasa_energy_rate == 0.18
    assert s.kasa_energy_currency == "€"
    assert s.kasa_state_file == Path("/data/known.json")


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    # Precedence must stay env > dotenv so Docker's injected vars always win.
    dotenv = tmp_path / ".env"
    dotenv.write_text("KASA_ENERGY_CURRENCY=FROM_DOTENV\n")
    monkeypatch.setenv("KASA_ENERGY_CURRENCY", "FROM_ENV")

    s = Settings(_env_file=str(dotenv))

    assert s.kasa_energy_currency == "FROM_ENV"


def test_dotenv_read_when_env_unset(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("KASA_ENERGY_CURRENCY=FROM_DOTENV\n")
    monkeypatch.delenv("KASA_ENERGY_CURRENCY", raising=False)

    s = Settings(_env_file=str(dotenv))

    assert s.kasa_energy_currency == "FROM_DOTENV"


def test_dotenv_strips_single_quotes(tmp_path, monkeypatch):
    # The user's real .env single-quotes TPLINK_PASSWORD (needed when
    # shell-sourcing); python-dotenv strips the matching quotes.
    dotenv = tmp_path / ".env"
    dotenv.write_text("TPLINK_PASSWORD='p@ss w#rd!'\n")
    monkeypatch.delenv("TPLINK_PASSWORD", raising=False)

    s = Settings(_env_file=str(dotenv))

    assert s.tplink_password == "p@ss w#rd!"


# ── invalid values warn and fall back (never crash) ──────────────────────────


def test_invalid_energy_rate_warns_and_disables(caplog):
    with caplog.at_level(logging.WARNING):
        s = _settings(kasa_energy_rate="not-a-number")
    assert s.kasa_energy_rate is None
    assert "KASA_ENERGY_RATE" in caplog.text


def test_blank_energy_rate_is_none_without_warning(caplog):
    with caplog.at_level(logging.WARNING):
        s = _settings(kasa_energy_rate="")
    assert s.kasa_energy_rate is None
    assert "KASA_ENERGY_RATE" not in caplog.text


def test_invalid_cloud_poll_interval_warns_and_defaults(caplog):
    with caplog.at_level(logging.WARNING):
        s = _settings(kasa_cloud_poll_interval="soon")
    assert s.kasa_cloud_poll_interval == 30.0
    assert "KASA_CLOUD_POLL_INTERVAL" in caplog.text


def test_invalid_sample_interval_warns_and_defaults(caplog):
    with caplog.at_level(logging.WARNING):
        s = _settings(kasa_energy_sample_interval="often")
    assert s.kasa_energy_sample_interval == 300.0
    assert "KASA_ENERGY_SAMPLE_INTERVAL" in caplog.text


def test_sample_interval_floored_at_ten():
    assert (
        _settings(kasa_energy_sample_interval="1").kasa_energy_sample_interval == 10.0
    )


def test_cloud_poll_interval_floored_at_zero():
    assert _settings(kasa_cloud_poll_interval="-5").kasa_cloud_poll_interval == 0.0


def test_invalid_port_warns_and_defaults(caplog):
    # Behaviour change: garbage KASA_PORT used to crash startup; now it warns
    # and falls back to 8080 for consistency with the other numeric knobs.
    with caplog.at_level(logging.WARNING):
        s = _settings(kasa_port="abc")
    assert s.kasa_port == 8080
    assert "KASA_PORT" in caplog.text


# ── cloud models parsing ─────────────────────────────────────────────────────


def test_cloud_models_splits_and_trims():
    s = _settings(kasa_cloud_models="HS300, KP303 ,")
    assert s.cloud_models == ("HS300", "KP303")


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_cloud_fallback_truthy(value):
    assert _settings(kasa_cloud_fallback=value).kasa_cloud_fallback is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "banana"])
def test_cloud_fallback_falsy(value):
    assert _settings(kasa_cloud_fallback=value).kasa_cloud_fallback is False
