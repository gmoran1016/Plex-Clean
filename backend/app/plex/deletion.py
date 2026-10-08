"""Freshness-checked deletion of one Plex media version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.models import DeleteResult, DuplicateGroup, ExternalMediaId, IdentityCandidate, LibrarySummary, PartInfo
from app.plex.client import PlexClient
from app.plex.identity import build_identity_candidates, external_identity_ids
from app.plex.library import normalize_media_item
from app.sessions import AppSession


def _metadata(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    container = payload.get("MediaContainer", payload)
    if not isinstance(container, dict):
        return None
    metadata = container.get("Metadata")
    if isinstance(metadata, list):
        return metadata[0] if metadata and isinstance(metadata[0], dict) else None
    return metadata if isinstance(metadata, dict) else None


def _part_identity(group: DuplicateGroup, media_id: str) -> tuple[tuple[str | None, str | None], ...] | None:
    selected = next((version for version in group.media_versions if version.media_id == media_id), None)
    if selected is None:
        return None
    return tuple(sorted(((part.part_id, part.file) for part in selected.parts), key=lambda item: (item[0] or "", item[1] or "")))


@dataclass(frozen=True, slots=True)
class _FreshEntry:
    group: DuplicateGroup | None
    identities: tuple[ExternalMediaId, ...]


class DeletionService:
    def __init__(self, client: PlexClient | None = None) -> None:
        self.client = client or PlexClient()

    async def _fetch_entry(
        self,
        session: AppSession,
        rating_key: str,
        library: LibrarySummary,
    ) -> _FreshEntry | None:
        response = await self.client.request(
            "GET",
            f"/library/metadata/{quote(rating_key, safe='')}",
            session=session,
            params={"includeGuids": 1, "includeMedia": 1, "includeStreams": 1},
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail="Plex could not refresh this item before deletion.")
        try:
            item = _metadata(response.json())
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Plex returned an unreadable item response.") from exc
        if item is None:
            return None
        return _FreshEntry(
            group=normalize_media_item(item, library, session),
            identities=external_identity_ids(item),
        )

    async def delete_version(
        self,
        session: AppSession,
        rating_key: str,
        media_id: str,
        *,
        server_id: str,
        scan_id: str,
        library_id: str,
        confirmed_parts: list[PartInfo],
        reviewed_identity: ExternalMediaId | None,
    ) -> DeleteResult:
        if session.selected_server is None or session.selected_server_id is None:
            raise HTTPException(status_code=409, detail="Choose a Plex server before deleting a version.")
        if session.selected_server.allow_media_deletion is not True:
            detail = (
                "Plex reports that media deletion is disabled on this server."
                if session.selected_server.allow_media_deletion is False
                else "Plex did not confirm that media deletion is enabled. Nothing was deleted."
            )
            raise HTTPException(status_code=403, detail=detail)
        scan = session.scan_progress
        snapshot = next(
            (group for group in session.duplicate_groups.get(library_id, []) if group.rating_key == rating_key),
            None,
        )
        if snapshot is None:
            snapshot = next(
                (
                    entry
                    for candidate in session.identity_candidates.get(library_id, [])
                    for entry in candidate.entries
                    if entry.rating_key == rating_key
                ),
                None,
            )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="This duplicate item is no longer in the current scan.")
        if (
            server_id != session.selected_server_id
            or scan is None
            or scan.state != "complete"
            or scan_id != scan.scan_id
            or library_id != snapshot.library_id
        ):
            self._raise_stale_confirmation(snapshot)

        if reviewed_identity is not None and not self._reviewed_candidate_exists(
            session, library_id, rating_key, reviewed_identity
        ):
            self._raise_stale_confirmation(snapshot)

        baseline_parts = _part_identity(snapshot, media_id)
        if baseline_parts is None:
            raise HTTPException(status_code=404, detail="This media version is no longer in the current scan.")
        selected_snapshot = next(version for version in snapshot.media_versions if version.media_id == media_id)
        if not selected_snapshot.parts or any(not part.part_id or not part.file for part in selected_snapshot.parts):
            raise HTTPException(
                status_code=409,
                detail="Plex did not provide complete part IDs and paths for this version. Nothing was deleted; rescan before trying again.",
            )
        confirmed_part_identity = tuple(
            sorted(
                ((part.part_id, part.file) for part in confirmed_parts if part.part_id and part.file),
                key=lambda item: (item[0] or "", item[1] or ""),
            )
        )
        if (
            len(confirmed_part_identity) != len(confirmed_parts)
            or confirmed_part_identity != baseline_parts
        ):
            self._raise_stale_confirmation(snapshot)
        library = next((item for item in session.libraries if item.library_id == snapshot.library_id), None)
        if library is None:
            raise HTTPException(status_code=404, detail="The Plex library is no longer available.")

        fresh_entry = await self._fetch_entry(session, rating_key, library)
        fresh_group = fresh_entry.group if fresh_entry else None
        identities = fresh_entry.identities if fresh_entry else ()
        fresh_parts = _part_identity(fresh_group, media_id) if fresh_group else None
        identity_matches = reviewed_identity is None or reviewed_identity in identities
        if fresh_parts is None or fresh_parts != baseline_parts or not identity_matches:
            self._replace_group(session, snapshot, fresh_group)
            self._replace_identity_candidates(session, rating_key, fresh_group, identities)
            library.duplicate_count = len(session.duplicate_groups.get(library.library_id, []))
            library.identity_candidate_count = len(session.identity_candidates.get(library.library_id, []))
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Plex item changed since this scan. Nothing was deleted; review the refreshed versions.",
                    "current_group": self._duplicate_group_payload(fresh_group),
                },
            )

        try:
            response = await self.client.request(
                "DELETE",
                f"/library/metadata/{quote(rating_key, safe='')}/media/{quote(media_id, safe='')}",
                session=session,
                params={"proxy": 0},
                timeout=60.0,
            )
        except HTTPException as exc:
            if exc.status_code == 504:
                raise HTTPException(
                    status_code=504,
                    detail="Plex delete request timed out and may have completed. No automatic retry was made; rescan before choosing again.",
                ) from exc
            raise
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="Plex denied deletion. A server administrator may be required.")
        if response.status_code == 404:
            raise HTTPException(status_code=409, detail="Plex no longer has this version. Refresh the library before choosing again.")
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail="Plex could not confirm deletion of this version. Run a new scan before choosing again.")

        refreshed_entry: _FreshEntry | None
        refresh_warning = False
        try:
            refreshed_entry = await self._fetch_entry(session, rating_key, library)
        except HTTPException:
            # Preserve the last safe snapshot and ask the UI to rescan; a read
            # failure cannot prove how many versions Plex now reports.
            refreshed_entry = _FreshEntry(group=snapshot, identities=())
            refresh_warning = True
        refreshed = refreshed_entry.group if refreshed_entry else None
        if not refresh_warning:
            self._replace_group(session, snapshot, refreshed)
            self._replace_identity_candidates(
                session,
                rating_key,
                refreshed,
                refreshed_entry.identities if refreshed_entry else (),
            )
            library.duplicate_count = len(session.duplicate_groups.get(library.library_id, []))
            library.identity_candidate_count = len(session.identity_candidates.get(library.library_id, []))
        if refreshed is not None and len(refreshed.media_versions) < 2:
            refreshed = None
        return DeleteResult(deleted=True, group=refreshed, library=library, refresh_warning=refresh_warning)

    @staticmethod
    def _duplicate_group_payload(group: DuplicateGroup | None) -> dict[str, Any] | None:
        if group is None or len(group.media_versions) < 2:
            return None
        return group.model_dump(mode="json")

    @classmethod
    def _raise_stale_confirmation(cls, snapshot: DuplicateGroup) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This confirmation no longer matches the current scan and file paths. Nothing was deleted; review the refreshed results.",
                "current_group": cls._duplicate_group_payload(snapshot),
            },
        )

    @staticmethod
    def _reviewed_candidate_exists(
        session: AppSession,
        library_id: str,
        rating_key: str,
        identity: ExternalMediaId,
    ) -> bool:
        return any(
            candidate.matched_provider == identity.provider
            and candidate.matched_identifier == identity.identifier
            and any(entry.rating_key == rating_key for entry in candidate.entries)
            for candidate in session.identity_candidates.get(library_id, [])
        )

    @staticmethod
    def _candidate_matches(
        candidate: IdentityCandidate,
        identities: tuple[ExternalMediaId, ...],
    ) -> bool:
        return any(
            identity.provider == candidate.matched_provider
            and identity.identifier == candidate.matched_identifier
            for identity in identities
        )

    @staticmethod
    def _replace_group(
        session: AppSession,
        previous: DuplicateGroup,
        current: DuplicateGroup | None,
    ) -> None:
        groups = session.duplicate_groups.get(previous.library_id, [])
        for index, group in enumerate(groups):
            if group.rating_key == previous.rating_key:
                if current is None or len(current.media_versions) < 2:
                    groups.pop(index)
                else:
                    groups[index] = current
                return
        if current is not None and len(current.media_versions) >= 2:
            groups.append(current)

    @classmethod
    def _replace_identity_candidates(
        cls,
        session: AppSession,
        rating_key: str,
        current: DuplicateGroup | None,
        identities: tuple[ExternalMediaId, ...],
    ) -> None:
        for library_id, candidates in list(session.identity_candidates.items()):
            updated: list[IdentityCandidate] = []
            for candidate in candidates:
                if not any(entry.rating_key == rating_key for entry in candidate.entries):
                    updated.append(candidate)
                    continue
                matching = cls._candidate_matches(candidate, identities)
                entries = [
                    current if entry.rating_key == rating_key and matching and current is not None else entry
                    for entry in candidate.entries
                    if entry.rating_key != rating_key or (matching and current is not None)
                ]
                library = LibrarySummary(
                    library_id=candidate.library_id,
                    title=candidate.library_title,
                    type=candidate.library_type,
                )
                identity = ExternalMediaId(
                    provider=candidate.matched_provider,
                    identifier=candidate.matched_identifier,
                )
                refreshed_candidates = build_identity_candidates(
                    [(entry, (identity,)) for entry in entries],
                    library,
                )
                updated.extend(refreshed_candidates)
            session.identity_candidates[library_id] = updated
