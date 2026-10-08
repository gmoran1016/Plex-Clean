from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.main import create_app
from app.models import DuplicateGroup, IdentityCandidate, LibrarySummary, MediaVersion, ServerOption
from app.plex.identity import external_identity_ids
from app.plex.library import PlexLibraryService
from app.sessions import AppSession, ServerAccess, SessionStore


def media(media_id: str, *, part_id: str | None = None) -> dict[str, object]:
    return {
        "id": media_id,
        "duration": 60000,
        "container": "mkv",
        "Part": [{"id": part_id or f"part-{media_id}", "file": f"/{media_id}.mkv", "size": 100}],
    }


def item(
    rating_key: str,
    *,
    ids: tuple[str, ...] = (),
    media_count: int = 1,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ratingKey": rating_key,
        "title": f"Item {rating_key}",
        "year": 2020,
        "Guid": [{"id": identity} for identity in ids],
        "Media": [media(f"{rating_key}-{number}") for number in range(media_count)],
    }
    result.update(extra or {})
    return result


class FakePlexClient:
    def __init__(self, pages: dict[tuple[str, int], list[dict[str, object]]]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        session: AppSession,
        params: dict[str, object] | None = None,
    ) -> httpx.Response:
        del session
        request_params = params or {}
        self.requests.append((path, request_params))
        offset = int(request_params.get("X-Plex-Container-Start", 0))
        section_id = path.split("/")[3]
        return httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": self.pages.get((section_id, offset), [])}},
        )


def movie_library(library_id: str = "1") -> LibrarySummary:
    return LibrarySummary(library_id=library_id, title=f"Library {library_id}", type="movie")


def candidate(identifier: str, first_key: str) -> IdentityCandidate:
    library = movie_library()
    entries = [
        DuplicateGroup(
            rating_key=key,
            library_id=library.library_id,
            library_title=library.title,
            library_type=library.type,
            title=f"Entry {key}",
            media_versions=[MediaVersion(media_id=f"media-{key}")],
        )
        for key in (first_key, f"{first_key}-other")
    ]
    return IdentityCandidate(
        library_id=library.library_id,
        library_title=library.title,
        library_type=library.type,
        matched_provider="imdb",
        matched_identifier=identifier,
        edition_review="clear",
        entries=entries,
    )


class IdentityScanTests(unittest.IsolatedAsyncioTestCase):
    async def scan(self, pages: dict[tuple[str, int], list[dict[str, object]]], libraries: list[LibrarySummary]):
        client = FakePlexClient(pages)
        session = AppSession(libraries=libraries)
        service = PlexLibraryService(client)  # type: ignore[arg-type]
        progress = await service.start_scan(session)
        await session.scan_task
        return service, client, session, progress

    async def test_scan_builds_identity_candidates_from_paginated_item_guids(self) -> None:
        library = movie_library()
        shared_id = "imdb://tt100"
        first_page = [item("a", ids=(shared_id,)), item("b", ids=(shared_id,))]
        second_page = [item("c", ids=("tmdb://300",))]

        with patch("app.plex.library.PAGE_SIZE", 2):
            service, client, session, progress = await self.scan(
                {("1", 0): first_page, ("1", 2): second_page}, [library]
            )

        candidates = service.get_identity_candidates(session, "1", 0, 25)
        self.assertEqual(len(candidates), 1)
        self.assertEqual([entry.rating_key for entry in candidates[0].entries], ["a", "b"])
        self.assertEqual([len(entry.media_versions) for entry in candidates[0].entries], [1, 1])
        self.assertEqual(progress.identity_candidate_groups, 1)
        self.assertEqual(library.identity_candidate_count, 1)
        self.assertEqual(len(client.requests), 2)
        self.assertTrue(all(path == "/library/sections/1/all" for path, _ in client.requests))
        self.assertTrue(all(params.get("includeGuids") == 1 for _, params in client.requests))

    async def test_scan_counts_items_without_supported_ids(self) -> None:
        library = movie_library()
        rows = [
            item("a"),
            item(
                "b",
                extra={
                    "guid": "imdb://top-level",
                    "ParentGuid": [{"id": "tvdb://episode-parent"}],
                    "grandparentGuid": "tvdb://show-only",
                },
            ),
        ]

        _, _, _, progress = await self.scan({("1", 0): rows}, [library])

        self.assertEqual(progress.items_without_supported_ids, 2)
        self.assertEqual(library.missing_identity_id_count, 2)

    async def test_invalid_provider_placeholders_do_not_form_candidates_and_count_as_missing(self) -> None:
        library = movie_library()
        placeholder_ids = ("tmdb://null", "imdb://unknown", "tvdb://0")

        service, _, session, progress = await self.scan(
            {("1", 0): [item("a", ids=placeholder_ids), item("b", ids=placeholder_ids)]},
            [library],
        )

        self.assertEqual(service.get_identity_candidates(session, "1", 0, 10), [])
        self.assertEqual(progress.identity_candidate_groups, 0)
        self.assertEqual(library.identity_candidate_count, 0)
        self.assertEqual(progress.items_without_supported_ids, 2)
        self.assertEqual(library.missing_identity_id_count, 2)

    def test_external_ids_validate_provider_shapes_and_ignore_agent_query_metadata(self) -> None:
        identities = external_identity_ids({
            "Guid": [
                {"id": "imdb://TT123?lang=en#item"},
                {"id": "tmdb://movie/550?lang=en#metadata"},
                {"id": "tvdb://series/42"},
                {"id": "tmdb://null"},
                {"id": "imdb://unknown"},
                {"id": "tvdb://0"},
            ]
        })

        self.assertEqual(
            [(identity.provider, identity.identifier) for identity in identities],
            [("imdb", "tt123"), ("tmdb", "movie/550"), ("tvdb", "series/42")],
        )

    async def test_scan_does_not_change_plex_duplicate_group_count(self) -> None:
        library = movie_library()
        rows = [
            item("multi", ids=("imdb://tt200",), media_count=2),
            item("separate", ids=("imdb://tt200",)),
        ]

        service, _, session, progress = await self.scan({("1", 0): rows}, [library])

        self.assertEqual(library.duplicate_count, 1)
        self.assertEqual(progress.duplicate_groups, 1)
        self.assertEqual(len(session.duplicate_groups["1"]), 1)
        self.assertEqual(len(session.duplicate_groups["1"][0].media_versions), 2)
        self.assertEqual(library.identity_candidate_count, 1)
        self.assertEqual(progress.identity_candidate_groups, 1)
        self.assertEqual(len(service.get_identity_candidates(session, "1", 0, 10)), 1)

    async def test_same_provider_id_in_two_libraries_creates_separate_candidates(self) -> None:
        first_library = movie_library("1")
        second_library = movie_library("2")
        duplicate_id = "tmdb://500"
        pages = {
            ("1", 0): [item("a", ids=(duplicate_id,)), item("b", ids=(duplicate_id,))],
            ("2", 0): [item("c", ids=(duplicate_id,)), item("d", ids=(duplicate_id,))],
        }

        service, _, session, progress = await self.scan(pages, [first_library, second_library])

        self.assertEqual(first_library.identity_candidate_count, 1)
        self.assertEqual(second_library.identity_candidate_count, 1)
        self.assertEqual(progress.identity_candidate_groups, 2)
        self.assertEqual(len(service.get_identity_candidates(session, "1", 0, 10)), 1)
        self.assertEqual(len(service.get_identity_candidates(session, "2", 0, 10)), 1)

    async def test_rescan_replaces_previous_identity_candidates(self) -> None:
        library = movie_library()
        session = AppSession(libraries=[library])
        session.identity_candidates = {"1": [candidate("tt-old", "old")]}
        service = PlexLibraryService(FakePlexClient({}))  # type: ignore[arg-type]

        await service.start_scan(session)
        await session.scan_task

        self.assertEqual(session.identity_candidates, {"1": []})


class IdentityCandidateRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_candidate_route_paginates_and_rejects_unknown_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), None, False)
            app = create_app(settings)
            app.state.library_service = PlexLibraryService(FakePlexClient({}))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.get("/api/session")
                session_id = client.cookies.get(settings.session_cookie_name)
                session = app.state.session_store.get(session_id)
                self.assertIsNotNone(session)
                session.libraries = [movie_library()]
                session.identity_candidates = {
                    "1": [candidate("tt-first", "first"), candidate("tt-second", "second")]
                }

                page = await client.get(
                    "/api/identity-candidates",
                    params={"library_id": "1", "offset": 1, "limit": 1},
                )
                unknown = await client.get(
                    "/api/identity-candidates", params={"library_id": "missing"}
                )
                invalid_limit = await client.get(
                    "/api/identity-candidates", params={"library_id": "1", "limit": 101}
                )
                invalid_offset = await client.get(
                    "/api/identity-candidates", params={"library_id": "1", "offset": -1}
                )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()[0]["matched_identifier"], "tt-second")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(invalid_limit.status_code, 422)
        self.assertEqual(invalid_offset.status_code, 422)

    async def test_logout_clears_identity_candidate_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), None, False)
            app = create_app(settings)
            app.state.library_service = PlexLibraryService(FakePlexClient({}))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                status = (await client.get("/api/session")).json()
                session_id = client.cookies.get(settings.session_cookie_name)
                session = app.state.session_store.get(session_id)
                session.identity_candidates = {"1": [candidate("tt-delete-me", "a")]}

                response = await client.post(
                    "/api/auth/logout",
                    headers={
                        "x-csrf-token": status["csrf_token"],
                        "origin": "http://testserver",
                    },
                )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(session.identity_candidates, {})

    async def test_server_selection_clears_identity_candidate_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), None, False)
            app = create_app(settings)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                status = (await client.get("/api/session")).json()
                session_id = client.cookies.get(settings.session_cookie_name)
                session = app.state.session_store.get(session_id)
                session.server_options = [ServerOption(machine_identifier="server-1", name="Server")]
                session.server_access = {
                    "server-1": ServerAccess(
                        machine_identifier="server-1",
                        name="Server",
                        base_url="http://plex.example:32400",
                        access_token="test-token",
                    )
                }
                session.identity_candidates = {"1": [candidate("tt-old-server", "a")]}
                session.libraries = [movie_library()]

                response = await client.put(
                    "/api/servers/selection",
                    json={"machine_identifier": "server-1"},
                    headers={
                        "x-csrf-token": status["csrf_token"],
                        "origin": "http://testserver",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.identity_candidates, {})
        self.assertEqual(session.libraries, [])

    def test_expired_session_clears_identity_candidate_results(self) -> None:
        store = SessionStore(idle_timeout_seconds=0)
        session_id, session = store.get_or_create(None)
        session.identity_candidates = {"1": [candidate("tt-expired", "a")]}

        self.assertIsNone(store.get(session_id))
        self.assertEqual(session.identity_candidates, {})


if __name__ == "__main__":
    unittest.main()
