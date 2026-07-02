"""Single source of truth for backend configuration.

Every tunable the server reads used to be a scattered ``os.getenv`` call, which
meant the app only ever saw ``.env`` under Docker Compose (compose injects the
file via ``env_file``). A bare ``just run`` / ``just api-dev`` started with no
``TPLINK_USERNAME``/``TPLINK_PASSWORD``, so discovery silently found no
SMART-protocol devices. Consolidating into a ``pydantic-settings`` model makes
``.env`` load no matter how the process starts, and gives one typed place to see
every knob.

Precedence is pydantic-settings' default and intentional: real environment
variables win over ``.env``. Docker keeps passing the credentials as real env
vars, so containers behave exactly as before; ``.env`` only fills the gaps for
local runs.

Access is via :func:`get_settings`, a lazily-built process-wide singleton —
lazy so import order (and tests) can influence the environment before the first
read, cached so every module shares one instance. Tests build their own
``Settings(_env_file=None)`` (see the loader functions' ``settings`` params and
the ``conftest`` fixture) so a developer's real repo-root ``.env`` can never
leak in and skew results.
"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging_config import get_logger

logger = get_logger(__name__)

# Cloud RPC identity defaults. These aren't in the README's Configuration table;
# they mirror what the Kasa/Tapo Android app sends and rarely need changing.
# Defined here (not in cloud_service) so they can seed the settings fields
# without a circular import — cloud_service imports them back from this module.
_DEFAULT_APP_TYPE = "Tapo_Android"
_DEFAULT_APP_VERSION = "2.8.14"

# Fallback values for the numeric knobs, kept as named constants so the
# warn-and-fall-back validators below and the field defaults agree.
_DEFAULT_CLOUD_POLL_INTERVAL = 30.0
_DEFAULT_ENERGY_SAMPLE_INTERVAL = 300.0
_MIN_ENERGY_SAMPLE_INTERVAL = 10.0
_DEFAULT_PORT = 8080


class Settings(BaseSettings):
    """All backend configuration, loaded from the environment and ``.env``.

    Field names are the lowercase of their environment variable, matched
    case-insensitively, so ``tplink_username`` reads ``TPLINK_USERNAME`` etc.
    Names and defaults match the README's Configuration table exactly — nothing
    here is renamed.
    """

    model_config = SettingsConfigDict(
        # Load ``.env`` from the current working directory (repo root under both
        # `just run` and Docker). Real env vars still take precedence.
        env_file=".env",
        env_file_encoding="utf-8",
        # python-dotenv strips matching surrounding quotes, so a single-quoted
        # TPLINK_PASSWORD (required when shell-sourcing .env) arrives unquoted.
        # Ignore unrelated env vars rather than erroring on them.
        extra="ignore",
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
    # Last-known identity of every device that has been read, so a device that
    # later drops off discovery is still shown (grayed, non-interactive) instead
    # of vanishing from its rooms/favorites. Sits beside the host store.
    kasa_snapshot_file: Path = Path("data/device_snapshots.json")
    kasa_groups_file: Path = Path("data/groups.json")
    kasa_energy_history_file: Path = Path("data/energy_history.db")

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
    # Serve in-process fake devices instead of scanning the network, so the
    # browser end-to-end smoke test can exercise real API wiring with no Kasa
    # hardware or credentials. Off by default; production startup is untouched.
    kasa_fake_devices: bool = False

    @property
    def cloud_models(self) -> tuple[str, ...]:
        """``kasa_cloud_models`` split into a tuple, dropping blank entries."""
        return tuple(m.strip() for m in self.kasa_cloud_models.split(",") if m.strip())

    # ── Validators ───────────────────────────────────────────────────────────
    # pydantic raises on validation failure by default, but the historical
    # behaviour for these knobs is to log a warning and fall back to the default
    # so a typo in a dotenv can't take the server down. ``mode="before"`` lets us
    # intercept the raw string. Defaults aren't validated, so an *unset* var uses
    # the field default without hitting these — only a set-but-bad value warns.

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
        # Historically ``int(os.getenv("KASA_PORT"))`` crashed startup on garbage.
        # For consistency with the other numeric knobs we now warn and fall back
        # to 8080 rather than raising (a behaviour change, but a safer default).
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
        # Preserve the exact historical truthy set: only these strings enable the
        # cloud path; anything else (including garbage) is off, never an error.
        # pydantic's own bool coercion would raise on unrecognised strings, so we
        # do the check ourselves.
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")


# Process-wide cache. Built on first use rather than at import so tests (and
# import order) can set the environment first. ``set_settings``/``reset_settings``
# are test hooks for overriding it without mutating ``os.environ``.
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
