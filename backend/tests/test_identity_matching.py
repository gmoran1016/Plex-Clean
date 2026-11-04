from __future__ import annotations

import unittest

from app.models import DuplicateGroup, LibrarySummary, MediaVersion, PartInfo
from app.plex.identity import ExternalMediaId, build_identity_candidates, external_identity_ids
from app.plex.library import mark_shared_episode_files


class ExternalIdentityIdTests(unittest.TestCase):
    def test_external_identity_ids_accepts_supported_item_guid_ids_only(self) -> None:
        item = {
            "guid": "imdb://top-level-must-not-match",
            "Guid": [
                {"id": "imdb://tt1234567"},
                {"id": "tmdb://98765"},
                {"id": "tvdb://12345"},
                {"id": "plex://movie/5d7768c54b7e8b001f1ab111"},
                {"id": "com.plexapp.agents.imdb://tt7654321"},
                {"id": "imdb://"},
                {"id": "tmdb://   "},
                {"id": "tvdb://bad id"},
                {"id": "not-a-uri"},
                {"id": ""},
                {"id": None},
                "imdb://tt9999999",
            ],
            "ParentGuid": [{"id": "imdb://parent-only"}],
            "grandparentGuid": "tvdb://show-only",
        }

        self.assertEqual(
            external_identity_ids(item),
            (
                ExternalMediaId(provider="imdb", identifier="tt1234567"),
                ExternalMediaId(provider="tmdb", identifier="98765"),
                ExternalMediaId(provider="tvdb", identifier="12345"),
            ),
        )

    def test_external_identity_ids_normalizes_scheme_and_ignores_query_and_fragment(self) -> None:
        item = {
            "Guid": [
                {"id": "IMDB://tt1234567?source=agent#metadata"},
                {"id": "TmDb://movie/98765?language=en#summary"},
            ]
        }

        self.assertEqual(
            external_identity_ids(item),
            (
                ExternalMediaId(provider="imdb", identifier="tt1234567"),
                ExternalMediaId(provider="tmdb", identifier="movie/98765"),
            ),
        )


def movie_library(library_id: str = "movies") -> LibrarySummary:
    return LibrarySummary(library_id=library_id, title=f"Library {library_id}", type="movie")


def entry(
    rating_key: str,
    *,
    library_id: str = "movies",
    library_type: str = "movie",
    editions: tuple[str | None, ...] = (None,),
    title: str | None = None,
    year: int | None = None,
) -> DuplicateGroup:
    return DuplicateGroup(
        rating_key=rating_key,
        library_id=library_id,
        library_title=f"Library {library_id}",
        library_type=library_type,
        title=title or f"Title {rating_key}",
        year=year,
        media_versions=[MediaVersion(media_id=f"{rating_key}-{n}", edition_title=edition) for n, edition in enumerate(editions)],
    )


class CandidateGroupingTests(unittest.TestCase):
    def test_shared_episode_file_paths_are_marked_across_entries(self) -> None:
        first = entry("e1", library_id="shows", library_type="show")
        second = entry("e2", library_id="shows", library_type="show")
        unrelated = entry("e3", library_id="shows", library_type="show")
        first.media_versions[0].parts = [PartInfo(part_id="p1", file="/TV/Show/Block.mkv")]
        second.media_versions[0].parts = [PartInfo(part_id="p2", file="\\TV\\Show\\Block.mkv")]
        unrelated.media_versions[0].parts = [PartInfo(part_id="p3", file="/TV/Show/Episode3.mkv")]

        mark_shared_episode_files([first, second, unrelated])

        self.assertEqual(first.media_versions[0].shared_episode_count, 1)
        self.assertEqual(second.media_versions[0].shared_episode_count, 1)
        self.assertEqual(unrelated.media_versions[0].shared_episode_count, 0)

    def test_movie_entries_group_only_on_a_shared_external_id(self) -> None:
        library = movie_library()
        imdb = ExternalMediaId(provider="imdb", identifier="tt123")
        tmdb = ExternalMediaId(provider="tmdb", identifier="123")
        first = entry("1", title="Same", year=2020)
        second = entry("2", title="Same", year=2020)
        unrelated = entry("3", title="Same", year=2020)

        candidates = build_identity_candidates(
            [
                (first, (imdb, tmdb)),
                (second, (imdb, tmdb)),
                (unrelated, (ExternalMediaId(provider="imdb", identifier="tt999"),)),
            ],
            library,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].matched_provider, "imdb")
        self.assertEqual(candidates[0].matched_identifier, "tt123")
        self.assertEqual([member.rating_key for member in candidates[0].entries], ["1", "2"])
        self.assertEqual(len(candidates[0].entries[0].media_versions), 1)

    def test_episode_group_ignores_parent_and_show_guids(self) -> None:
        library = LibrarySummary(library_id="shows", title="Shows", type="show")
        first = entry("e1", library_id="shows", library_type="show")
        second = entry("e2", library_id="shows", library_type="show")
        parent_only_first = {
            "Guid": [],
            "ParentGuid": [{"id": "tvdb://episode-77"}],
            "grandparentGuid": "tvdb://series-1",
        }
        parent_only_second = {
            "Guid": [],
            "ParentGuid": [{"id": "tvdb://episode-77"}],
            "grandparentGuid": "tvdb://series-1",
        }

        self.assertEqual(
            build_identity_candidates(
                [
                    (first, external_identity_ids(parent_only_first)),
                    (second, external_identity_ids(parent_only_second)),
                ],
                library,
            ),
            [],
        )

        episode = ExternalMediaId(provider="tvdb", identifier="episode-77")
        candidates = build_identity_candidates([(first, (episode,)), (second, (episode,))], library)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].matched_identifier, "episode-77")

    def test_library_and_media_type_are_part_of_identity_scope(self) -> None:
        library = movie_library()
        identity = ExternalMediaId(provider="tmdb", identifier="5")
        in_library = entry("m1")
        other_library = entry("m2", library_id="other")
        wrong_type = entry("m3", library_type="show")

        candidates = build_identity_candidates(
            [(in_library, (identity,)), (other_library, (identity,)), (wrong_type, (identity,))],
            library,
        )

        self.assertEqual(candidates, [])

    def test_partial_id_buckets_do_not_merge_transitively(self) -> None:
        library = movie_library()
        imdb = ExternalMediaId(provider="imdb", identifier="tt1")
        tmdb = ExternalMediaId(provider="tmdb", identifier="1")
        tvdb = ExternalMediaId(provider="tvdb", identifier="1")

        candidates = build_identity_candidates(
            [
                (entry("a"), (imdb, tmdb)),
                (entry("b"), (imdb, tvdb)),
                (entry("c"), (tmdb, tvdb)),
            ],
            library,
        )

        self.assertEqual(len(candidates), 3)
        self.assertEqual(
            {frozenset(member.rating_key for member in candidate.entries) for candidate in candidates},
            {frozenset({"a", "b"}), frozenset({"a", "c"}), frozenset({"b", "c"})},
        )

    def test_edition_review_marks_conflicting_and_partial_labels(self) -> None:
        library = movie_library()
        identity = ExternalMediaId(provider="imdb", identifier="tt1")

        conflict = build_identity_candidates(
            [
                (entry("a", editions=("Theatrical",)), (identity,)),
                (entry("b", editions=("Director's Cut",)), (identity,)),
            ],
            library,
        )
        partial = build_identity_candidates(
            [
                (entry("c", editions=("Theatrical",)), (identity,)),
                (entry("d", editions=(None,)), (identity,)),
            ],
            library,
        )
        clear = build_identity_candidates(
            [
                (entry("e", editions=("Theatrical",)), (identity,)),
                (entry("f", editions=("Theatrical",)), (identity,)),
            ],
            library,
        )
        unlabelled = build_identity_candidates(
            [(entry("g"), (identity,)), (entry("h"), (identity,))], library
        )

        self.assertEqual(conflict[0].edition_review, "conflict")
        self.assertEqual(partial[0].edition_review, "unknown")
        self.assertEqual(clear[0].edition_review, "clear")
        self.assertEqual(unlabelled[0].edition_review, "clear")


if __name__ == "__main__":
    unittest.main()
