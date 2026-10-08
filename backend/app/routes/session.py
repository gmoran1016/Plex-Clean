"""Session status API."""

from fastapi import APIRouter, Depends

from app.models import SessionStatus
from app.sessions import AppSession, get_current_session

router = APIRouter(prefix="/api", tags=["session"])


@router.get("/session", response_model=SessionStatus)
def get_session_status(session: AppSession = Depends(get_current_session)) -> SessionStatus:
    return SessionStatus(
        authenticated=session.authenticated,
        auth_pending=session.pending_auth is not None,
        username=session.username,
        csrf_token=session.csrf_token,
        selected_server=session.selected_server,
        libraries=session.libraries,
        scan=session.scan_progress,
    )
