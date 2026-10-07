"""Plex library enumeration and duplicate-version normalization."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.models import (
    DuplicateGroup,
    ExternalMediaId,
    IdentityCandidate,
    LibrarySummary,
    MediaVersion,
    PartInfo,
    ScanError,
    ScanProgress,
    StreamInfo,
)
from app.plex.client import PlexClient
from app.plex.identity import build_identity_candidates, external_identity_ids
from app.sessions import AppSession

PAGE_SIZE = 100


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _container(payload: Any) -> dict[str, Any]:
    return _as_dict(_as_dict(payload).get("MediaContainer", payload))


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _stream_type(value: Any) -> str:
    return {1: "video", 2: "audio", 3: "subtitle"}.get(_integer(value), "unknown")


def normalize_media(item: dict[str, Any], media: dict[str, Any]) -> MediaVersion:
    raw_parts = _as_list(media.get("Part"))
    parts = [
        PartInfo(
            part_id=_text(part.get("id")),
            file=_text(part.get("file")),
            size_bytes=_integer(part.get("size")),
            container=_text(part.get("container")),
        )
        for part in raw_parts
    ]
    sizes = [part.size_bytes for part in parts]
    total_size = sum(sizes) if sizes and all(size is not None for size in sizes) else None
    raw_streams = [stream for part in raw_parts for stream in _as_list(part.get("Stream"))]
    streams = [
        StreamInfo(
            stream_type=_stream_type(stream.get("streamType")),
            codec=_text(stream.get("codec")),
            language=_text(stream.get("language") or stream.get("languageTag")),
            channels=_integer(stream.get("channels")),
            channel_layout=_text(stream.get("channelLayout")),
            width=_integer(stream.get("width")),
            height=_integer(stream.get("height")),
            frame_rate=_text(stream.get("frameRate")),
            profile=_text(stream.get("profile")),
            dynamic_range=_text(stream.get("DOVIPresent") or stream.get("colorTrc")),
        )
        for stream in raw_streams
    ]
    video_stream = next((stream for stream in streams if stream.stream_type == "video"), None)
    audio_stream = next((stream for stream in streams if stream.stream_type == "audio"), None)
    media_container = _text(media.get("container")) or next((part.container for part in parts if part.container), None)
    return MediaVersion(
        media_id=_text(media.get("id")),
        edition_title=_text(media.get("editionTitle") or item.get("editionTitle")),
        duration_ms=_integer(media.get("duration") or item.get("duration")),
        width=_integer(media.get("width")) or (video_stream.width if video_stream else None),
        height=_integer(media.get("height")) or (video_stream.height if video_stream else None),
        bitrate_kbps=_integer(media.get("bitrate")),
        container=media_container,
        video_codec=_text(media.get("videoCodec")) or (video_stream.codec if video_stream else None),
        audio_codec=_text(media.get("audioCodec")) or (audio_stream.codec if audio_stream else None),
        audio_channels=_integer(media.get("audioChannels")) or (audio_stream.channels if audio_stream else None),
        file_size_bytes=total_size,
        parts=parts,
        streams=streams,
    )


def normalize_media_item(
    item: dict[str, Any],
    library: LibrarySummary,
    session: AppSession,
) -> DuplicateGroup | None:
    media_items = _as_list(item.get("Media"))
    rating_key = _text(item.get("ratingKey"))
    if not media_items or rating_key is None:
        return None
    versions = [normalize_media(item, media) for media in media_items]
    thumb = _text(item.get("thumb"))
    if thumb and thumb.startswith("/") and not thumb.startswith("//"):
        session.poster_paths[rating_key] = thumb
    return DuplicateGroup(
        rating_key=rating_key,
        library_id=library.library_id,
        library_title=library.title,
        library_type=library.type,
        title=str(item.get("title") or "Untitled Plex item"),
        year=_integer(item.get("year")),
        parent_title=_text(item.get("grandparentTitle")),
        season_index=_integer(item.get("parentIndex")),
        episode_index=_integer(item.get("index")),
        poster_url=f"/api/poster/{quote(rating_key, safe='')}" if rating_key in session.poster_paths else None,
        media_versions=versions,
    )


def normalize_group_item(
    item: dict[str, Any],
    library: LibrarySummary,
    session: AppSession,
) -> DuplicateGroup | None:
    group = normalize_media_item(item, library, session)
    if group is None or len(group.media_versions) < 2:
        return None
    return group


class PlexLibraryService:
    def __init__(self, client: PlexClient | None = None) -> None:
        self.client = client or PlexClient()

    async def _json(self, session: AppSession, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.request("GET", path, session=session, params=params)
        if response.status_code >= 400:
            detail = "Plex could not read this library."
            if response.status_code == 403:
                detail = "Plex denied access to this library."
            elif response.status_code == 404:
                detail = "This Plex library is no longer available."
            raise HTTPException(status_code=response.status_code, detail=detail)
        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Plex returned an unreadable library response.") from exc

    async def list_sections(self, session: AppSession) -> list[LibrarySummary]:
        payload = await self._json(session, "/library/sections")
        container = _container(payload)
        directories = _as_list(container.get("Directory"))
        result: list[LibrarySummary] = []
        for section in directories:
            kind = str(section.get("type", "")).lower()
            if kind not in {"movie", "show"}:
                continue
            section_id = _text(section.get("key") or section.get("id"))
            if section_id is None:
                continue
            previous = next((item for item in session.libraries if item.library_id == section_id), None)
            result.append(
                LibrarySummary(
                    library_id=section_id,
                    title=str(section.get("title") or f"{kind.title()} library"),
                    type=kind,
                    item_count=previous.item_count if previous else (_integer(section.get("size")) or 0),
                    duplicate_count=previous.duplicate_count if previous else 0,
                    identity_candidate_count=previous.identity_candidate_count if previous else 0,
                    missing_identity_id_count=previous.missing_identity_id_count if previous else 0,
                    scan_state=previous.scan_state if previous else "idle",
                )
            )
        session.libraries = result
        return result

    async def start_scan(self, session: AppSession) -> ScanProgress:
        task = session.scan_task
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail="A library scan is already running.")
        if not session.libraries:
            await self.list_sections(session)
        scan_id = secrets.token_urlsafe(18)
        progress = ScanProgress(
            scan_id=scan_id,
            state="pending",
            total_libraries=len(session.libraries),
        )
        session.scan_progress = progress
        session.duplicate_groups = {library.library_id: [] for library in session.libraries}
        session.identity_candidates = {library.library_id: [] for library in session.libraries}
        session.poster_paths.clear()
        for library in session.libraries:
            library.identity_candidate_count = 0
            library.missing_identity_id_count = 0
            library.scan_state = "pending"
        session.scan_task = asyncio.create_task(self._run_scan(session, scan_id))
        return progress

    async def _run_scan(self, session: AppSession, scan_id: str) -> None:
        progress = session.scan_progress
        if progress is None or progress.scan_id != scan_id:
            return
        progress.state = "running"
        failed_libraries = 0
        for library in list(session.libraries):
            progress.current_library_id = library.library_id
            progress.current_library_title = library.title
            groups: list[DuplicateGroup] = []
            identity_entries: list[tuple[DuplicateGroup, tuple[ExternalMediaId, ...]]] = []
            item_count = 0
            missing_identity_id_count = 0
            try:
                offset = 0
                while True:
                    content_type = "1" if library.type == "movie" else "4"
                    payload = await self._json(
                        session,
                        f"/library/sections/{quote(library.library_id, safe='')}/all",
                        params={
                            "type": content_type,
                            "includeGuids": 1,
                            "includeMedia": 1,
                            "includeStreams": 1,
                            "X-Plex-Container-Start": offset,
                            "X-Plex-Container-Size": PAGE_SIZE,
                        },
                    )
                    container = _container(payload)
                    items = _as_list(container.get("Metadata"))
                    if not items:
                        break
                    for item in items:
                        item_count += 1
                        progress.items_scanned += 1
                        identities = external_identity_ids(item)
                        if not identities:
                            missing_identity_id_count += 1
                            progress.items_without_supported_ids += 1
                        normalized = normalize_media_item(item, library, session)
                        if normalized is None:
                            continue
                        if len(normalized.media_versions) >= 2:
                            groups.append(normalized)
                        if identities:
                            identity_entries.append((normalized, identities))
                    offset += len(items)
                    # `size` is commonly just the current page length. Only
                    # `totalSize` is safe to use as the end-of-results marker.
                    total = _integer(container.get("totalSize"))
                    if len(items) < PAGE_SIZE or (total is not None and offset >= total):
                        break
                candidates = build_identity_candidates(identity_entries, library)
                session.duplicate_groups[library.library_id] = groups
                session.identity_candidates[library.library_id] = candidates
                library.item_count = item_count
                library.duplicate_count = len(groups)
                library.identity_candidate_count = len(candidates)
                library.missing_identity_id_count = missing_identity_id_count
                library.scan_state = "complete"
                progress.duplicate_groups += len(groups)
                progress.identity_candidate_groups += len(candidates)
            except asyncio.CancelledError:
                raise
            except HTTPException as exc:
                failed_libraries += 1
                library.scan_state = "failed"
                progress.errors.append(
                    ScanError(
                        library_id=library.library_id,
                        library_title=library.title,
                        message=str(exc.detail),
                    )
                )
            except Exception:
                failed_libraries += 1
                library.scan_state = "failed"
                progress.errors.append(
                    ScanError(
                        library_id=library.library_id,
                        library_title=library.title,
                        message="Unexpected error while scanning this library.",
                    )
                )
            finally:
                progress.libraries_complete += 1
        progress.current_library_id = None
        progress.current_library_title = None
        progress.state = "failed" if progress.errors and failed_libraries == progress.total_libraries else "complete"

    def get_scan(self, session: AppSession, scan_id: str) -> ScanProgress:
        progress = session.scan_progress
        if progress is None or progress.scan_id != scan_id:
            raise HTTPException(status_code=404, detail="This scan is not available in the current session.")
        return progress

    def get_duplicates(self, session: AppSession, library_id: str, offset: int, limit: int) -> list[DuplicateGroup]:
        if not any(library.library_id == library_id for library in session.libraries):
            raise HTTPException(status_code=404, detail="Plex library was not found in this session.")
        groups = session.duplicate_groups.get(library_id, [])
        return groups[offset : offset + limit]

    def get_identity_candidates(
        self,
        session: AppSession,
        library_id: str,
        offset: int,
        limit: int,
    ) -> list[IdentityCandidate]:
        if not any(library.library_id == library_id for library in session.libraries):
            raise HTTPException(status_code=404, detail="Plex library was not found in this session.")
        candidates = session.identity_candidates.get(library_id, [])
        return candidates[offset : offset + limit]
