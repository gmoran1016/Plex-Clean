"""CSRF-protected, single-version deletion API."""

from fastapi import APIRouter, Depends, Request

from app.models import DeleteResult, DeleteVersionRequest
from app.plex.auth import PlexAuthService
from app.plex.deletion import DeletionService
from app.plex.client import PlexClient
from app.sessions import AppSession, require_csrf

router = APIRouter(prefix="/api", tags=["deletion"])


@router.post("/duplicates/{rating_key}/media/{media_id}/delete", response_model=DeleteResult)
async def delete_version(
    rating_key: str,
    media_id: str,
    request: Request,
    confirmation: DeleteVersionRequest,
    session: AppSession = Depends(require_csrf),
) -> DeleteResult:
    auth = PlexAuthService(
        request.app.state.settings,
        request.app.state.plex_client_identifier,
        request.app.state.plex_device_private_key,
    )
    return await DeletionService(PlexClient(auth)).delete_version(
        session,
        rating_key,
        media_id,
        server_id=confirmation.server_id,
        scan_id=confirmation.scan_id,
        library_id=confirmation.library_id,
        confirmed_parts=confirmation.confirmed_parts,
        reviewed_identity=confirmation.reviewed_identity,
    )
