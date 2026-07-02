"""Single source of truth for backend configuration.

A ``pydantic-settings`` model so ``.env`` loads no matter how the process starts
(not just under Docker Compose's ``env_file``) — otherwise a bare ``just run``
has no ``TPLINK_USERNAME``/``TPLINK_PASSWORD`` and discovery silently finds no
SMART-protocol devices.

Precedence is intentional: real env vars win over ``.env``, so Docker (which
passes credentials as real env vars) behaves as before and ``.env`` only fills
gaps for local runs.

Access via :func:`get_settings`, a lazy process-wide singleton — lazy so import
order and tests can set the environment first, cached so every module shares one
instance. Tests build their own ``Settings(_env_file=None)`` so a developer's
repo-root ``.env`` can't leak in.
"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging_config import get_logger

logger = get_logger(__name__)

# Cloud RPC identity defaults: mirror the Kasa/Tapo Android app, rarely changed.
# Here (not in cloud_service) to seed the settings fields without a circular
# import — cloud_service imports them back.
_DEFAULT_APP_TYPE = "Tapo_Android"
_DEFAULT_APP_VERSION = "2.8.14"

# Numeric-knob fallbacks as named constants so the validators and field defaults
# agree.
_DEFAULT_CLOUD_POLL_INTERVAL = 30.0
_DEFAULT_ENERGY_SAMPLE_INTERVAL = 300.0
_MIN_ENERGY_SAMPLE_INTERVAL = 10.0
_DEFAULT_PORT = 8080


class Settings(BaseSettings):
    """All backend configuration, loaded from the environment and ``.env``.

    Field names are the lowercased env var, matched case-insensitively, so
    ``tplink_username`` reads ``TPLINK_USERNAME``. Names/defaults match the
    README's Configuration table.
    """

    model_config = SettingsConfigDict(
        # Load ``.env`` from the CWD (repo root under `just run` and Docker); real
        # env vars still take precedence.
        env_file=".env",
        env_file_encoding="utf-8",
        # python-dotenv strips matching surrounding quotes, so a single-quoted
        # TPLINK_PASSWORD (required when shell-sourcing .env) arrives unquoted.
        extra="ignore",  # ignore unrelated env vars rather than erroring
        case_sensitive=False,
    )

    # ── TP-Link credentials ──────────────────────────────────────────────────
    # Required for newer SMART-protocol devices; legacy plugs work without them.
    tplink_username: str | None = None
    tplink_password: str | None = None

    # ── Networking / server ──────────────────────────────────────────────────
    kasa_host: str = "127.0.0.1"
    kasa_port: int = _DEFAULT_PORT
    # Optional CIDR swept by unicast on startup, for devices on a separate VLAN
    # that broadcast discovery can't reach. None disables the sweep.
    kasa_scan_subnet: str | None = None

    # ── Persistence paths (mount these as volumes to survive rebuilds) ────────
    kasa_state_file: Path = Path("data/known_devices.json")
    # Last-known identity of every read device, so one that drops off discovery
    # stays shown (grayed) instead of vanishing from its rooms/favorites.
    kasa_snapshot_file: Path = Path("data/device_snapshots.json")
    kasa_groups_file: Path = Path("data/groups.json")
    kasa_energy_history_file: Path = Path("data/energy_history.db")
    # Server-side schedule rules ("at HH:MM on these days, turn X on/off").
    kasa_schedules_file: Path = Path("data/schedules.json")

    # ── Energy history / cost ────────────────────────────────────────────────
    kasa_energy_sample_interval: float = _DEFAULT_ENERGY_SAMPLE_INTERVAL
    # Optional flat $/kWh rate; None hides cost (kWh only). Its currency prefix.
    kasa_energy_rate: float | None = None
    kasa_energy_currency: str = "$"

    # ── Cloud fallback (opt-in; sends credentials to TP-Link's servers) ───────
    kasa_cloud_fallback: bool = False
    # Comma-separated model prefixes routed through the cloud. Exposed parsed as
    # a tuple via the ``cloud_models`` property below.
    kasa_cloud_models: str = "HS300"
    kasa_cloud_poll_interval: float = _DEFAULT_CLOUD_POLL_INTERVAL
    kasa_cloud_app_type: str = _DEFAULT_APP_TYPE
    kasa_cloud_app_version: str = _DEFAULT_APP_VERSION
    kasa_cloud_terminal_uuid: str | None = None

    # ── Test-only seams ──────────────────────────────────────────────────────
    # Serve in-process fake devices instead of scanning, so the browser e2e test
    # exercises real API wiring with no hardware/credentials. Off by default.
    kasa_fake_devices: bool = False

    @property
    def cloud_models(self) -> tuple[str, ...]:
        """``kasa_cloud_models`` split into a tuple, dropping blank entries."""
        return tuple(m.strip() for m in self.kasa_cloud_models.split(",") if m.strip())

    # ── Validators ───────────────────────────────────────────────────────────
    # These knobs warn and fall back to the default rather than raising, so a
    # dotenv typo can't take the server down. ``mode="before"`` intercepts the
    # raw string; an *unset* var uses the field default without hitting these, so
    # only a set-but-bad value warns.

    @field_validator("kasa_energy_rate", mode="before")
    @classmethod
    def _parse_energy_rate(cls, value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            logger.warning(
                f"Ignoring invalid KASA_ENERGY_RATE={value!r}; expected a number"
            )
            return None

    @field_validator("kasa_cloud_poll_interval", mode="before")
    @classmethod
    def _parse_cloud_poll_interval(cls, value: object) -> float:
        if value is None:
            return _DEFAULT_CLOUD_POLL_INTERVAL
        text = str(value).strip()
        if not text:
            return _DEFAULT_CLOUD_POLL_INTERVAL
        try:
            return max(0.0, float(text))
        except ValueError:
            logger.warning(
                f"Ignoring invalid KASA_CLOUD_POLL_INTERVAL={value!r}; using 30s"
            )
            return _DEFAULT_CLOUD_POLL_INTERVAL

    @field_validator("kasa_energy_sample_interval", mode="before")
    @classmethod
    def _parse_sample_interval(cls, value: object) -> float:
        # Floored at 10s so a misconfigured tiny value can't busy-loop.
        if value is None:
            return _DEFAULT_ENERGY_SAMPLE_INTERVAL
        text = str(value).strip()
        if not text:
            return _DEFAULT_ENERGY_SAMPLE_INTERVAL
        try:
            return max(_MIN_ENERGY_SAMPLE_INTERVAL, float(text))
        except ValueError:
            logger.warning(
                f"Ignoring invalid KASA_ENERGY_SAMPLE_INTERVAL={value!r}; using 300s"
            )
            return _DEFAULT_ENERGY_SAMPLE_INTERVAL

    @field_validator("kasa_port", mode="before")
    @classmethod
    def _parse_port(cls, value: object) -> int:
        # Warn and fall back to 8080 rather than raising, so garbage can't crash
        # startup (matches the other numeric knobs).
        if value is None:
            return _DEFAULT_PORT
        text = str(value).strip()
        if not text:
            return _DEFAULT_PORT
        try:
            return int(text)
        except ValueError:
            logger.warning(f"Ignoring invalid KASA_PORT={value!r}; using 8080")
            return _DEFAULT_PORT

    @field_validator("kasa_cloud_fallback", "kasa_fake_devices", mode="before")
    @classmethod
    def _parse_cloud_fallback(cls, value: object) -> bool:
        # Only these strings enable the flag; anything else is off, never an error
        # (pydantic's own bool coercion would raise on unrecognised strings).
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")


# Process-wide cache, built on first use so tests and import order can set the
# environment first. set_settings/reset_settings override it without touching
# os.environ.
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the shared :class:`Settings`, building it on first access."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Replace the cached settings (or clear it with ``None``). Test hook."""
    global _settings
    _settings = settings


def reset_settings() -> None:
    """Drop the cached settings so the next :func:`get_settings` rebuilds. Test hook."""
    set_settings(None)
