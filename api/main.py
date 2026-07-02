import asyncio
import contextlib
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import get_settings
from .energy_history import history, load_sample_interval, run_recorder
from .events import router as events_router
from .kasa_service import registry
from .logging_config import get_logger, setup_logging
from .routes import router

logger = get_logger(__name__)

try:
    __version__ = version("kasa-nice")
except PackageNotFoundError:  # not installed (e.g. running from a bare checkout)
    __version__ = "0.0.0"

# Built SvelteKit SPA (web/build). Present in production images; absent in dev,
# where the frontend is served by the Vite dev server instead.
WEB_BUILD_DIR = Path(__file__).resolve().parent.parent / "web" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Starting Kasa-Nice API")
    # Test-only seam, enabled via KASA_FAKE_DEVICES (see Settings.kasa_fake_devices).
    if get_settings().kasa_fake_devices:
        # Test-only seam: seed the registry with fakes instead of scanning the
        # network. One of them flips its state on every read, so the SSE stream's
        # periodic re-reads surface a server-initiated change the browser never
        # triggered — the live-update case the smoke test asserts. The energy
        # recorder is skipped to keep the fake run hermetic (no history writes).
        from .testing.fake_devices import seed_registry

        logger.warning("KASA_FAKE_DEVICES set; serving in-process fake devices")
        seed_registry(registry)
        tasks: tuple[asyncio.Task, ...] = ()
    else:
        # Discovery (broadcast + subnet sweep + cloud) can take many seconds, so
        # run it in the background and let the API serve immediately. The frontend
        # watches registry.discovering (via /api/status) and surfaces devices as
        # they appear, instead of blocking on an empty list at startup.
        discovery = asyncio.create_task(registry.run_startup_discovery())
        # Sample metered devices on an interval and persist the readings, so
        # energy history survives beyond what each device remembers.
        recorder = asyncio.create_task(
            run_recorder(registry, history, load_sample_interval())
        )
        tasks = (discovery, recorder)
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
