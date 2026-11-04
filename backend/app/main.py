"""FastAPI application entry point."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.auth_storage import PlexCredentialStore
from app.config import Settings
from app.routes.auth import router as auth_router
from app.routes.deletion import router as deletion_router
from app.routes.library import router as library_router
from app.routes.session import router as session_router
from app.sessions import SessionStore


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.plex_client_identifier = app_settings.load_or_create_client_identifier()
        app.state.plex_device_private_key = app_settings.load_or_create_plex_device_key()
        app.state.plex_credential_store.prune(time.time())
        yield

    app = FastAPI(title="Plex-Clean", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.plex_credential_store = PlexCredentialStore(app_settings.config_dir)
    app.state.session_store = SessionStore(
        app_settings.session_idle_timeout_seconds,
        credentials=app.state.plex_credential_store,
    )

    @app.middleware("http")
    async def attach_api_session(request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        store: SessionStore = request.app.state.session_store
        old_session_id = request.cookies.get(app_settings.session_cookie_name)
        session_id, session = store.get_or_create(old_session_id)
        session.client_identifier = getattr(request.app.state, "plex_client_identifier", None)
        request.state.app_session = session
        request.state.app_session_id = session_id
        response = await call_next(request)
        store.persist_authentication_if_needed(session_id, session)
        response.set_cookie(
            key=app_settings.session_cookie_name,
            value=session_id,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            secure=app_settings.session_cookie_secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/healthz", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(session_router)
    app.include_router(auth_router)
    app.include_router(library_router)
    app.include_router(deletion_router)
    static_directory = Path(__file__).with_name("static")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")
    else:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def frontend_not_built() -> str:
            return "<main><h1>Plex-Clean API is running</h1><p>Run the Vite development server or build the frontend.</p></main>"
    return app


app = create_app()
