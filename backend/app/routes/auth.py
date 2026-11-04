"""Plex authentication and server selection routes."""

import secrets
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from app.models import AuthStart, AuthStatus, ServerOption
from app.plex.auth import PlexAuthService, PlexResourceService
from app.sessions import AppSession, get_current_session, require_csrf

router = APIRouter(prefix="/api", tags=["auth"])


def _browser_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    is_referer = not origin
    candidate = origin or request.headers.get("referer") or ""
    try:
        parsed = urlsplit(candidate)
        parsed.port  # Force malformed ports to fail validation.
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Could not determine the Plex-Clean browser origin.") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (not is_referer and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment))
    ):
        raise HTTPException(status_code=400, detail="Could not determine the Plex-Clean browser origin.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def _auth_service(request: Request) -> PlexAuthService:
    identifier = getattr(request.app.state, "plex_client_identifier", None)
    if not identifier:
        raise HTTPException(status_code=503, detail="Plex-Clean is still starting. Try again shortly.")
    device_private_key = getattr(request.app.state, "plex_device_private_key", None)
    if device_private_key is None:
        raise HTTPException(status_code=503, detail="Plex-Clean's device signing key is not ready yet.")
    return PlexAuthService(request.app.state.settings, identifier, device_private_key)


@router.post("/auth/pin", response_model=AuthStart)
async def start_pin(
    request: Request,
    session: AppSession = Depends(require_csrf),
) -> AuthStart:
    return await _auth_service(request).start_pin(session, browser_origin=_browser_origin(request))


@router.get("/auth/status", response_model=AuthStatus)
async def auth_status(
    request: Request,
    session: AppSession = Depends(get_current_session),
) -> AuthStatus:
    auth = _auth_service(request)
    status = await auth.poll_pin(session) if session.pending_auth else AuthStatus(
        authenticated=session.authenticated,
        pending=False,
        username=session.username,
        servers_available=len(session.server_options),
    )
    if session.authenticated and not session.server_options:
        servers = await PlexResourceService(auth).list_servers(session)
        status.servers_available = len(servers)
        if not servers:
            status.message = "No reachable Plex Media Server was found. Check container network access or set PLEX_SERVER_URL."
    return status


@router.post("/auth/logout", status_code=204)
def logout(request: Request, session: AppSession = Depends(require_csrf)) -> None:
    session_id = getattr(request.state, "app_session_id", None)
    if isinstance(session_id, str):
        request.app.state.plex_credential_store.delete(session_id)
    session.clear_sensitive()
    session.csrf_token = secrets.token_urlsafe(32)


@router.get("/servers", response_model=list[ServerOption])
async def get_servers(
    request: Request,
    session: AppSession = Depends(get_current_session),
) -> list[ServerOption]:
    if not session.authenticated:
        raise HTTPException(status_code=401, detail="Sign in to Plex first.")
    return await PlexResourceService(_auth_service(request)).list_servers(session)


@router.put("/servers/selection", response_model=ServerOption)
def select_server(
    body: dict[str, str],
    session: AppSession = Depends(require_csrf),
) -> ServerOption:
    machine_id = body.get("machine_identifier", "")
    server = next((item for item in session.server_options if item.machine_identifier == machine_id), None)
    if server is None or machine_id not in session.server_access:
        raise HTTPException(status_code=404, detail="That reachable Plex server is not available in this session.")
    session.selected_server_id = machine_id
    session.libraries.clear()
    session.scan_progress = None
    session.duplicate_groups.clear()
    session.identity_candidates.clear()
    return server
