from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.main import create_app
from app.models import (
    DeleteResult,
    DuplicateGroup,
    ExternalMediaId,
    IdentityCandidate,
    LibrarySummary,
    MediaVersion,
    PartInfo,
    ScanProgress,
    ServerOption,
)
from app.plex.deletion import DeletionService
from app.sessions import AppSession


def library() -> LibrarySummary:
    return LibrarySummary(library_id="1", title="Movies", type="movie")


def version(media_id: str, part_id: str, path: str, edition: str | None = None) -> MediaVersion:
    return MediaVersion(
        media_id=media_id,
        edition_title=edition,
        parts=[PartInfo(part_id=part_id, file=path, size_bytes=100, container="mkv")],
    )


def group(rating_key: str, versions: list[MediaVersion]) -> DuplicateGroup:
    return DuplicateGroup(
        rating_key=rating_key,
        library_id="1",
        library_title="Movies",
        library_type="movie",
        title=f"Movie {rating_key}",
        year=2020,
        media_versions=versions,
    )


def identity_candidate(
    entries: list[DuplicateGroup], provider: str = "imdb", identifier: str = "tt100"
) -> IdentityCandidate:
    return IdentityCandidate(
        library_id="1",
        library_title="Movies",
        library_type="movie",
        matched_provider=provider,  # type: ignore[arg-type]
        matched_identifier=identifier,
        edition_review="clear",
        entries=entries,
    )


def plex_media(media_id: str, part_id: str | None, path: str | None, edition: str | None = None):
    part: dict[str, object] = {"id": part_id, "file": path, "size": 100, "container": "mkv"}
    parts = [part] if part_id is not None or path is not None else []
    return {"id": media_id, "container": "mkv", "editionTitle": edition, "Part": parts}


def fresh_item(
    rating_key: str,
    identities: tuple[str, ...],
    media_items: list[dict[str, object]],
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "MediaContainer": {
                "Metadata": [
                    {
                        "ratingKey": rating_key,
                        "title": f"Movie {rating_key}",
                        "year": 2020,
                        "Guid": [{"id": identity} for identity in identities],
                        "Media": media_items,
                    }
                ]
            }
        },
    )


class FakePlexClient:
    def __init__(self, fresh_responses: list[httpx.Response]) -> None:
        self.fresh_responses = fresh_responses
        self.calls: list[tuple[str, str, dict[str, object], float | None]] = []

    async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        self.calls.append(
            (
                method,
                path,
                kwargs.get("params", {}),  # type: ignore[arg-type]
                kwargs.get("timeout"),  # type: ignore[arg-type]
            )
        )
        if method == "DELETE":
            return httpx.Response(200)
        return self.fresh_responses.pop(0)


def session_for(
    *,
    candidates: list[IdentityCandidate] | None = None,
    duplicate_groups: list[DuplicateGroup] | None = None,
) -> AppSession:
    session = AppSession()
    session.server_options = [
        ServerOption(machine_identifier="server-1", name="Test Plex", allow_media_deletion=True)
    ]
    session.selected_server_id = "server-1"
    session.libraries = [library()]
    session.identity_candidates = {"1": candidates or []}
    session.duplicate_groups = {"1": duplicate_groups or []}
    session.scan_progress = ScanProgress(scan_id="scan-1", state="complete")
    session.libraries[0].identity_candidate_count = len(session.identity_candidates["1"])
    session.libraries[0].duplicate_count = len(session.duplicate_groups["1"])
    return session


def confirmation(
    group_snapshot: DuplicateGroup,
    media_id: str,
    *,
    scan_id: str = "scan-1",
    reviewed_identity: ExternalMediaId | None = None,
) -> dict[str, object]:
    selected = next(version for version in group_snapshot.media_versions if version.media_id == media_id)
    return {
        "server_id": "server-1",
        "scan_id": scan_id,
        "library_id": group_snapshot.library_id,
        "confirmed_parts": [PartInfo(part_id=part.part_id, file=part.file) for part in selected.parts],
        "reviewed_identity": reviewed_identity,
    }


class IdentityDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_episode_file_is_rejected_without_plex_delete(self) -> None:
        selected = version("a-media", "part-a", "/a.mkv")
        selected.shared_episode_count = 1
        snapshot = group("a", [selected]).model_copy(update={"library_type": "show"})
        session = session_for(duplicate_groups=[snapshot])
        client = FakePlexClient([fresh_item("a", (), [plex_media("a-media", "part-a", "/a.mkv")])])

        with self.assertRaises(HTTPException) as raised:
            await DeletionService(client).delete_version(
                session,
                "a",
                "a-media",
                **confirmation(snapshot, "a-media"),  # type: ignore[arg-type]
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(any(method == "DELETE" for method, *_ in client.calls))

    async def test_delete_route_rejects_a_stale_confirmation_body_without_plex_delete(self) -> None:
        old = group("a", [version("a-media", "part-old", "/old.mkv")])
        current = group("a", [version("a-media", "part-new", "/new.mkv")])
        fake = FakePlexClient([])

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(Path(directory), None, False))
            app.state.plex_client_identifier = "test-client"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.get("/api/session")
                session_id = client.cookies.get("plexclean_session")
                session = app.state.session_store.get(session_id)
                self.assertIsNotNone(session)
                session.server_options = [
                    ServerOption(machine_identifier="server-1", name="Test Plex", allow_media_deletion=True)
                ]
                session.selected_server_id = "server-1"
                session.libraries = [library()]
                session.scan_progress = ScanProgress(scan_id="scan-2", state="complete")
                session.duplicate_groups = {"1": [current]}
                body = confirmation(old, "a-media", scan_id="scan-1")
                body["confirmed_parts"] = [{"part_id": "part-old", "file": "/old.mkv"}]
                headers = {
                    "X-CSRF-Token": session.csrf_token,
                    "Origin": "http://testserver",
                }

                with patch("app.routes.deletion.PlexClient", return_value=fake):
                    response = await client.post(
                        "/api/duplicates/a/media/a-media/delete",
                        headers=headers,
                        json=body,
                    )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(fake.calls)

    async def test_identity_member_delete_checks_exact_media_and_parts(self) -> None:
        selected = group("a", [version("a-media", "part-a", "/a.mkv")])
        peer = group("b", [version("b-media", "part-b", "/b.mkv")])
        candidate = identity_candidate([selected, peer])
        session = session_for(candidates=[candidate])
        current_item = fresh_item("a", ("imdb://tt100",), [plex_media("a-media", "part-a", "/a.mkv")])
        client = FakePlexClient([current_item, current_item])

        result = await DeletionService(client).delete_version(
            session, "a", "a-media", **confirmation(selected, "a-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100"))
        )  # type: ignore[arg-type]

        delete_calls = [call for call in client.calls if call[0] == "DELETE"]
        self.assertTrue(result.deleted)
        self.assertEqual(len(delete_calls), 1)
        self.assertEqual(delete_calls[0][1], "/library/metadata/a/media/a-media")
        get_calls = [call for call in client.calls if call[0] == "GET"]
        self.assertTrue(all(call[2].get("includeGuids") == 1 for call in get_calls))
        self.assertEqual(len(session.identity_candidates["1"]), 1)

    async def test_changed_identity_member_part_aborts_before_delete_and_refreshes_candidate(self) -> None:
        for refreshed_media in (
            plex_media("a-media", "part-new", "/new.mkv"),
            plex_media("a-media", None, None),
        ):
            with self.subTest(refreshed_media=refreshed_media):
                selected = group("a", [version("a-media", "part-a", "/a.mkv")])
                peer = group("b", [version("b-media", "part-b", "/b.mkv")])
                session = session_for(candidates=[identity_candidate([selected, peer])])
                client = FakePlexClient([fresh_item("a", ("imdb://tt100",), [refreshed_media])])

                with self.assertRaises(HTTPException) as raised:
                    await DeletionService(client).delete_version(
                        session, "a", "a-media", **confirmation(selected, "a-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100"))
                    )  # type: ignore[arg-type]

                self.assertEqual(raised.exception.status_code, 409)
                self.assertFalse(any(call[0] == "DELETE" for call in client.calls))
                refreshed = session.identity_candidates["1"][0].entries[0].media_versions[0]
                if refreshed_media["Part"]:
                    self.assertEqual(refreshed.parts[0].file, "/new.mkv")
                else:
                    self.assertEqual(refreshed.parts, [])

    async def test_changed_identity_id_removes_member_from_candidate(self) -> None:
        selected = group("a", [version("a-media", "part-a", "/a.mkv")])
        peer = group("b", [version("b-media", "part-b", "/b.mkv")])
        session = session_for(candidates=[identity_candidate([selected, peer])])
        client = FakePlexClient(
            [fresh_item("a", ("imdb://tt-different",), [plex_media("a-media", "part-a", "/a.mkv")])]
        )

        with self.assertRaises(HTTPException) as raised:
            await DeletionService(client).delete_version(
                session, "a", "a-media", **confirmation(selected, "a-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100"))
            )  # type: ignore[arg-type]

        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(any(call[0] == "DELETE" for call in client.calls))
        self.assertEqual(session.identity_candidates["1"], [])
        self.assertEqual(session.libraries[0].identity_candidate_count, 0)

    async def test_overlapping_candidate_cannot_authorize_a_different_reviewed_identifier(self) -> None:
        selected = group("a", [version("a-media", "part-a", "/a.mkv")])
        imdb_peer = group("b", [version("b-media", "part-b", "/b.mkv")])
        tmdb_peer = group("c", [version("c-media", "part-c", "/c.mkv")])
        session = session_for(candidates=[
            identity_candidate([selected, imdb_peer], "imdb", "tt100"),
            identity_candidate([selected, tmdb_peer], "tmdb", "movie/200"),
        ])
        client = FakePlexClient([
            fresh_item("a", ("tmdb://movie/200",), [plex_media("a-media", "part-a", "/a.mkv")])
        ])

        with self.assertRaises(HTTPException) as raised:
            await DeletionService(client).delete_version(
                session,
                "a",
                "a-media",
                **confirmation(selected, "a-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100")),
            )  # type: ignore[arg-type]

        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(any(call[0] == "DELETE" for call in client.calls))

    async def test_confirmation_from_before_rescan_or_snapshot_refresh_is_rejected(self) -> None:
        for changed_scan in (False, True):
            with self.subTest(changed_scan=changed_scan):
                old = group("a", [version("a-media", "part-old", "/old.mkv")])
                refreshed = group("a", [version("a-media", "part-new", "/new.mkv")])
                session = session_for(duplicate_groups=[refreshed])
                old_confirmation = confirmation(old, "a-media")
                if changed_scan:
                    session.scan_progress = ScanProgress(scan_id="scan-2", state="complete")
                client = FakePlexClient([
                    fresh_item("a", (), [plex_media("a-media", "part-new", "/new.mkv")])
                ])

                with self.assertRaises(HTTPException) as raised:
                    await DeletionService(client).delete_version(
                        session, "a", "a-media", **old_confirmation
                    )  # type: ignore[arg-type]

                self.assertEqual(raised.exception.status_code, 409)
                self.assertFalse(client.calls)

    async def test_deleting_identity_member_updates_or_removes_candidate(self) -> None:
        selected = group(
            "a",
            [
                version("a-media", "part-a", "/a.mkv", "Theatrical"),
                version("a-media-2", "part-a2", "/a2.mkv", "Theatrical"),
            ],
        )
        peer = group("b", [version("b-media", "part-b", "/b.mkv", "Theatrical")])
        third = group("c", [version("c-media", "part-c", "/c.mkv", "Theatrical")])
        session = session_for(candidates=[identity_candidate([selected, peer, third])])
        before = fresh_item(
            "a",
            ("imdb://tt100",),
            [
                plex_media("a-media", "part-a", "/a.mkv", "Theatrical"),
                plex_media("a-media-2", "part-a2", "/a2.mkv", "Theatrical"),
            ],
        )
        after = fresh_item(
            "a",
            ("imdb://tt100",),
            [plex_media("a-media-2", "part-a2", "/a2.mkv", "Director's Cut")],
        )
        result = await DeletionService(FakePlexClient([before, after])).delete_version(  # type: ignore[arg-type]
            session,
            "a",
            "a-media",
            **confirmation(selected, "a-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100")),
        )

        self.assertTrue(result.deleted)
        updated = session.identity_candidates["1"][0]
        updated_member = next(entry for entry in updated.entries if entry.rating_key == "a")
        self.assertEqual([version.media_id for version in updated_member.media_versions], ["a-media-2"])
        self.assertEqual(updated.edition_review, "conflict")
        self.assertEqual(session.libraries[0].identity_candidate_count, 1)

        two_entry_session = session_for(candidates=[identity_candidate([peer, third])])
        delete_member = fresh_item(
            "b", ("imdb://tt100",), [plex_media("b-media", "part-b", "/b.mkv", "Theatrical")]
        )
        remove_client = FakePlexClient([delete_member, httpx.Response(404)])
        removed = await DeletionService(remove_client).delete_version(  # type: ignore[arg-type]
            two_entry_session,
            "b",
            "b-media",
            **confirmation(peer, "b-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100")),
        )

        self.assertTrue(removed.deleted)
        self.assertEqual(two_entry_session.identity_candidates["1"], [])
        self.assertEqual(two_entry_session.libraries[0].identity_candidate_count, 0)

    async def test_one_version_candidate_survivor_is_not_returned_as_a_plex_duplicate_group(self) -> None:
        selected = group(
            "a",
            [version("a-media", "part-a", "/a.mkv"), version("a-media-2", "part-a2", "/a2.mkv")],
        )
        peer = group("b", [version("b-media", "part-b", "/b.mkv")])
        session = session_for(candidates=[identity_candidate([selected, peer])])
        before = fresh_item(
            "a", ("imdb://tt100",), [plex_media("a-media", "part-a", "/a.mkv"), plex_media("a-media-2", "part-a2", "/a2.mkv")]
        )
        after = fresh_item("a", ("imdb://tt100",), [plex_media("a-media-2", "part-a2", "/a2.mkv")])

        result = await DeletionService(FakePlexClient([before, after])).delete_version(
            session,
            "a",
            "a-media",
            **confirmation(selected, "a-media", reviewed_identity=ExternalMediaId(provider="imdb", identifier="tt100")),
        )  # type: ignore[arg-type]

        self.assertIsNone(result.group)
        self.assertEqual(session.duplicate_groups["1"], [])
        self.assertEqual(session.libraries[0].duplicate_count, 0)
        updated = session.identity_candidates["1"][0]
        survivor = next(entry for entry in updated.entries if entry.rating_key == "a")
        self.assertEqual([item.media_id for item in survivor.media_versions], ["a-media-2"])

    async def test_existing_duplicate_group_delete_behavior_is_preserved(self) -> None:
        duplicate = group(
            "multi",
            [version("m1", "p1", "/one.mkv"), version("m2", "p2", "/two.mkv")],
        )
        session = session_for(duplicate_groups=[duplicate])
        before = fresh_item(
            "multi",
            (),
            [plex_media("m1", "p1", "/one.mkv"), plex_media("m2", "p2", "/two.mkv")],
        )
        after = fresh_item("multi", (), [plex_media("m2", "p2", "/two.mkv")])

        result: DeleteResult = await DeletionService(FakePlexClient([before, after])).delete_version(  # type: ignore[arg-type]
            session,
            "multi",
            "m1",
            **confirmation(duplicate, "m1"),
        )

        self.assertTrue(result.deleted)
        self.assertIsNone(result.group)
        self.assertEqual(session.duplicate_groups["1"], [])
        self.assertEqual(session.libraries[0].duplicate_count, 0)


if __name__ == "__main__":
    unittest.main()
