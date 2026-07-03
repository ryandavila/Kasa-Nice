"""Read and replace the vacation-mode (presence-simulation) config.

A single-document resource, so it's GET + PUT of the whole config (like
``/api/favorites``), not by-id CRUD. GET additionally folds in the engine's live,
server-derived status — whether the window is currently active and the soonest
planned switch — so the UI needs only one request to render the panel and its
header indicator.

Module-level names (``vacation_store``, ``engine``, ``groups``) are bound here so
tests can monkeypatch them per this router, exactly as the other routers do.
"""

from fastapi import APIRouter

from ..group_store import groups
from ..schemas import VacationConfig, VacationStatus
from ..vacation import engine, resolve_device_ids
from ..vacation_store import vacation_store

router = APIRouter(prefix="/api")


def _status(config: VacationConfig) -> VacationStatus:
    """Assemble the config plus the engine's live status into one response."""
    return VacationStatus(
        **config.model_dump(),
        active=engine.is_active(config),
        # Only meaningful while active; the engine already returns None when idle.
        next_switch_ts=engine.next_switch_ts(),
        resolved_device_ids=resolve_device_ids(config, groups),
    )


@router.get("/vacation", response_model=VacationStatus)
async def get_vacation() -> VacationStatus:
    """Current vacation config plus live engine status (active + next switch)."""
    return _status(vacation_store.load())


@router.put("/vacation", response_model=VacationStatus)
async def put_vacation(config: VacationConfig) -> VacationStatus:
    """Replace the whole vacation config; the running engine picks it up next tick.

    ``config`` is pydantic-validated (HH:MM shapes, coherent min/max interval), so
    a malformed payload is rejected before it reaches the file. No restart is
    needed: the loop reads the store fresh each tick.
    """
    saved = vacation_store.save(config)
    return _status(saved)
