import asyncio
import contextlib
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .alerts import (
    alert_center,
    alert_evaluator,
    alert_thresholds,
    run_alert_evaluator,
)
from .config import get_settings
from .energy_history import history, run_recorder
from .events import broadcaster
from .events import router as events_router
from .group_store import groups
from .kasa_service import registry
from .logging_config import get_logger, setup_logging
from .routes import router
from .schedule_store import schedules
from .scheduler import run_scheduler

logger = get_logger(__name__)

try:
    __version__ = version("kasa-nice")
except PackageNotFoundError:  # not installed (e.g. running from a bare checkout)
    __version__ = "0.0.0"

# Built SvelteKit SPA. Present in production images; absent in dev (Vite serves it).
WEB_BUILD_DIR = Path(__file__).resolve().parent.parent / "web" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Starting Kasa-Nice API")
    if get_settings().kasa_fake_devices:
        # Test-only seam: seed fakes instead of scanning. One flips its state on
        # every read, so the SSE stream surfaces a server-initiated change (the
        # smoke test's live-update case). Recorder and scheduler are skipped to
        # keep the fake run hermetic (no history/schedule writes).
        from .testing.fake_devices import seed_registry

        logger.warning("KASA_FAKE_DEVICES set; serving in-process fake devices")
        seed_registry(registry)
        tasks: tuple[asyncio.Task, ...] = ()
    else:
        # Discovery takes many seconds; run it in the background so the API serves
        # immediately (the frontend watches registry.discovering via /api/status).
        # Flag it BEFORE any sibling task can run: the alert evaluator skips its
        # cycles while discovering, and must not sneak in a first cycle against
        # the empty registry (it would seed everything unreachable, then fire a
        # spurious "recovered" storm when the sweep finishes).
        registry.discovering = True
        discovery = asyncio.create_task(registry.run_startup_discovery())
        settings = get_settings()
        # Sample metered devices and persist readings beyond device memory.
        recorder = asyncio.create_task(
            run_recorder(registry, history, settings.kasa_energy_sample_interval)
        )
        # Fire schedule rules on the local clock, so timers work for both local
        # and cloud devices and keep running with no browser open.
        scheduler = asyncio.create_task(
            run_scheduler(
                schedules,
                registry,
                groups,
                broadcaster,
                # Lets sunrise/sunset rules resolve; None (unset) => they don't fire.
                location=get_settings().location,
            )
        )
        # Evaluate the alert detectors (reachability + power thresholds) on their
        # own interval; delivers to the in-app ring buffer and optional webhook.
        alerts = asyncio.create_task(
            run_alert_evaluator(
                registry,
                alert_evaluator,
                alert_center,
                alert_thresholds,
                interval=settings.kasa_alert_interval,
                history=history,
                webhook_url=settings.kasa_alert_webhook_url,
            )
        )
        tasks = (discovery, recorder, scheduler, alerts)
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await registry.aclose()


def _mount_spa(app: FastAPI) -> None:
    """Serve the SvelteKit build, falling back to index.html for client routes."""
    index = WEB_BUILD_DIR / "index.html"

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        candidate = (WEB_BUILD_DIR / path).resolve()
        if candidate.is_file() and WEB_BUILD_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def create_app() -> FastAPI:
    app = FastAPI(title="Kasa-Nice API", version=__version__, lifespan=lifespan)

    # Allow the Vite dev server origin so the frontend can run standalone in dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(events_router)

    if WEB_BUILD_DIR.is_dir():
        _mount_spa(app)
    else:
        logger.warning(f"Frontend build not found at {WEB_BUILD_DIR}; serving API only")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    logger.info(f"Running on {settings.kasa_host}:{settings.kasa_port}")
    uvicorn.run(app, host=settings.kasa_host, port=settings.kasa_port)


if __name__ == "__main__":
    run()
