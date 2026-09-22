from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.agent.manager import RunManager
from app.api.mcp_server import build_mcp_server
from app.api.rest import router as rest_router
from app.browser.pool import SessionPool
from app.config import Settings, get_settings
from app.logging_setup import configure_logging
from app.search import SearxngSearch
from app.storage.repository import SqliteRunRepository
from app.storage.screenshots import ScreenshotStore

logger = logging.getLogger(__name__)

UI_PATH = Path(__file__).parent / "api" / "ui.html"
PUBLIC_PATHS = {"/health", "/", "/ui"}


@dataclass
class Container:
    settings: Settings
    pool: SessionPool
    repository: SqliteRunRepository
    screenshots: ScreenshotStore
    runs: RunManager
    search: Any | None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, keys: list[str]) -> None:
        super().__init__(app)
        self._keys = set(keys)

    async def dispatch(self, request: Request, call_next):
        if not self._keys or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-api-key") or _bearer(request)
        if provided not in self._keys:
            return JSONResponse({"detail": "invalid or missing api key"}, status_code=401)
        return await call_next(request)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    mcp_server = None
    mcp_app = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = SessionPool(settings)
        await pool.start()
        repository = SqliteRunRepository(settings.db_path)
        screenshots = ScreenshotStore(
            settings.screenshot_dir, settings.screenshot_retention_seconds
        )
        await screenshots.sweep()
        search = (
            SearxngSearch(settings.searxng_url, settings.searxng_timeout)
            if settings.searxng_url
            else None
        )
        container = Container(
            settings=settings,
            pool=pool,
            repository=repository,
            screenshots=screenshots,
            runs=RunManager(settings, pool, repository, screenshots, search),
            search=search,
        )
        app.state.container = container
        logger.info(
            "service started",
            extra={"max_sessions": settings.max_sessions, "search": search is not None},
        )
        try:
            if mcp_app is not None:
                async with mcp_app.router.lifespan_context(mcp_app):
                    yield
            else:
                yield
        finally:
            await container.runs.shutdown()
            await pool.shutdown()
            repository.close()

    app = FastAPI(
        title="control-browser",
        version="0.1.0",
        description="Lets an LLM operate a web browser: sessions, snapshots, actions and runs.",
        lifespan=lifespan,
    )
    app.include_router(rest_router)

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    async def debug_ui() -> Any:
        if not UI_PATH.exists():
            return PlainTextResponse("no debug ui bundled", status_code=404)
        return PlainTextResponse(UI_PATH.read_text(encoding="utf-8"), media_type="text/html")

    mcp_server = build_mcp_server(_LazyContainer(app))
    mcp_app = mcp_server.streamable_http_app(streamable_http_path="/mcp")
    app.mount("/mcp", mcp_app)

    app.add_middleware(ApiKeyMiddleware, keys=list(settings.api_keys))
    return app


class _LazyContainer:
    """Gives the MCP handlers the container once the lifespan has built it."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def __getattr__(self, item: str) -> Any:
        return getattr(self._app.state.container, item)


app = create_app()
