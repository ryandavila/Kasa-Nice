import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .kasa_service import registry
from .logging_config import get_logger, setup_logging
from .routes import router

logger = get_logger(__name__)

# Built SvelteKit SPA (web/build). Present in production images; absent in dev,
# where the frontend is served by the Vite dev server instead.
WEB_BUILD_DIR = Path(__file__).resolve().parent.parent / "web" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Starting Kasa-Nice API")
    try:
        await registry.discover_all()
    except Exception as e:  # noqa: BLE001 - never block startup on discovery
        logger.error(f"Initial discovery failed: {e}")
    yield


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
    app = FastAPI(title="Kasa-Nice API", version="1.1.0", lifespan=lifespan)

    # Allow the Vite dev server origin so the frontend can run standalone in dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    if WEB_BUILD_DIR.is_dir():
        _mount_spa(app)
    else:
        logger.warning(f"Frontend build not found at {WEB_BUILD_DIR}; serving API only")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    host = os.getenv("KASA_HOST", "127.0.0.1")
    port = int(os.getenv("KASA_PORT", "8080"))
    logger.info(f"Running on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
