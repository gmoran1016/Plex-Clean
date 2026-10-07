"""In-memory Plex sessions and request security dependencies."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from app.models import DuplicateGroup, IdentityCandidate, LibrarySummary, ScanProgress, ServerOption


@dataclass(slots=True)
class PendingPlexAuth:
    pin_id: int
    expires_at: float


@dataclass(slots=True)
class ServerAccess:
    machine_identifier: str
    name: str
    base_url: str
    access_token: str
    local: bool = False
    relay: bool = False


@dataclass(slots=True)
class AppSession:
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    last_activity: float = field(default_factory=time.monotonic)
    client_identifier: str | None = None
    pending_auth: PendingPlexAuth | None = None
    plex_token: str | None = None
    plex_token_expires_at: float | None = None
    username: str | None = None
    server_options: list[ServerOption] = field(default_factory=list)
    server_access: dict[str, ServerAccess] = field(default_factory=dict)
    selected_server_id: str | None = None
    libraries: list[LibrarySummary] = field(default_factory=list)
    scan_progress: ScanProgress | None = None
    duplicate_groups: dict[str, list[DuplicateGroup]] = field(default_factory=dict)
    identity_candidates: dict[str, list[IdentityCandidate]] = field(default_factory=dict)
    poster_paths: dict[str, str] = field(default_factory=dict)
    scan_task: Any | None = None

    @property
    def authenticated(self) -> bool:
        return self.plex_token is not None

    @property
    def selected_server(self) -> ServerOption | None:
        if self.selected_server_id is None:
            return None
        return next(
            (
                option
                for option in self.server_options
                if option.machine_identifier == self.selected_server_id
            ),
            None,
        )

    def clear_sensitive(self) -> None:
        task = self.scan_task
        if task is not None and hasattr(task, "cancel"):
            task.cancel()
        self.client_identifier = None
        self.pending_auth = None
        self.plex_token = None
        self.plex_token_expires_at = None
        self.username = None
        self.server_access.clear()
        self.server_options.clear()
        self.selected_server_id = None
        self.libraries.clear()
        self.scan_progress = None
        self.duplicate_groups.clear()
        self.identity_candidates.clear()
        self.poster_paths.clear()
        self.scan_task = None


class SessionStore:
    def __init__(self, idle_timeout_seconds: int = 24 * 60 * 60) -> None:
        self._idle_timeout_seconds = idle_timeout_seconds
        self._sessions: dict[str, AppSession] = {}
        self._lock = RLock()

    def _remove_expired(self, now: float) -> None:
        with self._lock:
            expired = [
                session_id
                for session_id, session in self._sessions.items()
                if now - session.last_activity >= self._idle_timeout_seconds
            ]
            for session_id in expired:
                self.clear(session_id)

    def get_or_create(self, cookie_value: str | None) -> tuple[str, AppSession]:
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            if cookie_value is not None:
                session = self._sessions.get(cookie_value)
                if session is not None:
                    session.last_activity = now
                    return cookie_value, session

            session_id = secrets.token_urlsafe(32)
            session = AppSession(last_activity=now)
            self._sessions[session_id] = session
            return session_id, session

    def get(self, session_id: str) -> AppSession | None:
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_activity = now
            return session

    def clear(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.clear_sensitive()


def get_current_session(request: Request) -> AppSession:
    session: AppSession | None = getattr(request.state, "app_session", None)
    if session is None:
        raise HTTPException(status_code=401, detail="Session is unavailable. Reload the page.")
    return session


def require_csrf(
    request: Request,
    session: AppSession = Depends(get_current_session),
) -> AppSession:
    supplied = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(supplied, session.csrf_token):
        raise HTTPException(status_code=403, detail="Request security token is missing or invalid.")

    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        origin = referer if referer is not None else None
    expected_host = request.headers.get("host", "").lower()
    actual_host = urlsplit(origin or "").netloc.lower()
    if not expected_host or actual_host != expected_host:
        raise HTTPException(status_code=403, detail="Cross-origin state changes are not allowed.")
    return session
