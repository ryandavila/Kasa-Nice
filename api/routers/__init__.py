"""Per-domain API routers, split out of the former monolithic ``routes`` module.

Each submodule defines its own ``router = APIRouter(prefix="/api")`` and binds its
own module-level names (``registry``, stores, ``broadcaster``) so tests can
monkeypatch them per domain, exactly as they did against the old ``routes``
module. ``api.main`` includes each router in turn.
"""

from ._helpers import _validated_rows
from .alerts import router as alerts_router
from .backup import router as backup_router
from .devices import router as devices_router
from .energy import router as energy_router
from .groups import router as groups_router
from .scenes import router as scenes_router
from .schedules import router as schedules_router
from .vacation import router as vacation_router

__all__ = [
    "_validated_rows",
    "alerts_router",
    "backup_router",
    "devices_router",
    "energy_router",
    "groups_router",
    "scenes_router",
    "schedules_router",
    "vacation_router",
]
