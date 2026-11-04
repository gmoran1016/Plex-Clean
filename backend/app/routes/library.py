"""Movie and TV library scanning and duplicate listing API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.models import DuplicateGroup, IdentityCandidate, LibrarySummary, ScanProgress
from app.plex.auth import PlexAuthService
from app.plex.client import PlexClient
from app.plex.library import PlexLibraryService
from app.sessions import AppSession, get_current_session, require_csrf

router = APIRouter(prefix="/api", tags=["library"])


def _service(request: Request) -> PlexLibraryService:
    service = getattr(request.app.state, "library_service", None)
    if service is None:
        service = PlexLibraryService(PlexClient(PlexAuthService(
            request.app.state.settings,
            request.app.state.plex_client_identifier,
            request.app.state.plex_device_private_key,
        )))
        request.app.state.library_service = service
    return service


@router.get("/libraries", response_model=list[LibrarySummary])
async def list_libraries(
    request: Request,
    session: AppSession = Depends(get_current_session),
) -> list[LibrarySummary]:
    return await _service(request).list_sections(session)


@router.post("/scan", response_model=ScanProgress)
async def start_scan(
    request: Request,
    session: AppSession = Depends(require_csrf),
) -> ScanProgress:
    return await _service(request).start_scan(session)


@router.get("/scan/{scan_id}", response_model=ScanProgress)
def get_scan(
    scan_id: str,
    request: Request,
    session: AppSession = Depends(get_current_session),
) -> ScanProgress:
    return _service(request).get_scan(session, scan_id)


@router.get("/duplicates", response_model=list[DuplicateGroup])
def get_duplicates(
    request: Request,
    library_id: str = Query(min_length=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    session: AppSession = Depends(get_current_session),
) -> list[DuplicateGroup]:
    return _service(request).get_duplicates(session, library_id, offset, limit)


@router.get("/identity-candidates", response_model=list[IdentityCandidate])
def get_identity_candidates(
    request: Request,
    library_id: str = Query(min_length=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    session: AppSession = Depends(get_current_session),
) -> list[IdentityCandidate]:
    return _service(request).get_identity_candidates(session, library_id, offset, limit)


@router.get("/poster/{rating_key}")
async def get_poster(
    rating_key: str,
    request: Request,
    session: AppSession = Depends(get_current_session),
) -> Response:
    path = session.poster_paths.get(rating_key)
    if not path or not path.startswith("/") or path.startswith("//"):
        raise HTTPException(status_code=404, detail="Poster is not available for this item.")
    auth = PlexAuthService(
        request.app.state.settings,
        request.app.state.plex_client_identifier,
        request.app.state.plex_device_private_key,
    )
    result = await PlexClient(auth).request("GET", path, session=session)
    if result.status_code >= 400:
        raise HTTPException(status_code=404, detail="Plex no longer has this poster.")
    content_type = result.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=502, detail="Plex returned an invalid poster response.")
    return Response(content=result.content, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})
