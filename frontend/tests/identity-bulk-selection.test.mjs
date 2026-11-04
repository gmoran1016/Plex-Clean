import assert from "node:assert/strict";
import test from "node:test";

import {
  analyzeSafeIdentityDuplicates,
  findCandidatesWithoutSurvivors,
  findDuplicateGroupsWithoutSurvivors,
} from "../src/identityBulkSelection.ts";

function version(id, width, height, bitrate, duration = 7_200_000, overrides = {}) {
  return {
    media_id: id,
    edition_title: null,
    duration_ms: duration,
    width,
    height,
    bitrate_kbps: bitrate,
    container: "mkv",
    video_codec: "h264",
    audio_codec: "aac",
    audio_channels: 2,
    file_size_bytes: 1_000_000,
    parts: [{ part_id: `part-${id}`, file: `/${id}.mkv`, size_bytes: 1_000_000, container: "mkv" }],
    streams: [],
    ...overrides,
  };
}

function entry(ratingKey, mediaVersion) {
  return {
    rating_key: ratingKey,
    library_id: "movies",
    library_title: "Movies",
    library_type: "movie",
    title: "Example Movie",
    year: 2020,
    parent_title: null,
    season_index: null,
    episode_index: null,
    poster_url: null,
    media_versions: [mediaVersion],
  };
}

function candidate(entries, overrides = {}) {
  return {
    library_id: "movies",
    library_title: "Movies",
    library_type: "movie",
    matched_provider: "imdb",
    matched_identifier: "tt1234567",
    match_reason: "metadata_id_match",
    edition_review: "clear",
    entries,
    ...overrides,
  };
}

test("selects lower-quality separate entries while preserving one clear best copy", () => {
  const group = candidate([
    entry("low", version("low-media", 1280, 720, 3_000)),
    entry("high", version("high-media", 1920, 1080, 8_000)),
    entry("mid", version("mid-media", 1920, 1080, 5_000)),
  ]);

  const result = analyzeSafeIdentityDuplicates([group]);

  assert.deepEqual(result.selected.map((item) => item.group.rating_key).sort(), ["low", "mid"]);
  assert.equal(result.selected.every((item) => item.reviewedIdentity?.identifier === "tt1234567"), true);
  assert.equal(result.skippedCandidates, 0);
  assert.equal(result.skippedVersions, 0);
  assert.deepEqual(findCandidatesWithoutSurvivors([group], new Set(result.selected.map((item) => item.key))), []);
});

test("skips candidate groups with edition, runtime, or quality ambiguity", () => {
  const editionConflict = candidate([
    entry("edition-a", version("edition-media-a", 1280, 720, 3_000)),
    entry("edition-b", version("edition-media-b", 1920, 1080, 8_000)),
  ], { edition_review: "conflict" });
  const runtimeConflict = candidate([
    entry("runtime-a", version("runtime-media-a", 1280, 720, 3_000, 7_000_000)),
    entry("runtime-b", version("runtime-media-b", 1920, 1080, 8_000, 5_000_000)),
  ], { matched_identifier: "tt1234568" });
  const unknownQuality = candidate([
    entry("unknown-a", version("unknown-media-a", null, null, 3_000)),
    entry("unknown-b", version("unknown-media-b", 1920, 1080, 8_000)),
  ], { matched_identifier: "tt1234569" });

  const result = analyzeSafeIdentityDuplicates([editionConflict, runtimeConflict, unknownQuality]);

  assert.deepEqual(result.selected, []);
  assert.equal(result.skippedCandidates, 3);
});

test("does not preselect a separate episode entry whose file is shared", () => {
  const group = candidate([
    entry("shared-low", version("shared-low-media", 1280, 720, 3_000, 7_200_000, { shared_episode_count: 1 })),
    entry("best", version("best-media", 1920, 1080, 8_000)),
  ], { library_type: "show" });

  const result = analyzeSafeIdentityDuplicates([group]);

  assert.deepEqual(result.selected, []);
  assert.equal(result.skippedVersions, 1);
});

test("skips tied best quality and overlapping external-ID candidates", () => {
  const tied = candidate([
    entry("tie-a", version("tie-media-a", 1920, 1080, 8_000)),
    entry("tie-b", version("tie-media-b", 1920, 1080, 8_000)),
  ], { matched_identifier: "tt1234570" });
  const sharedEntry = entry("overlap", version("overlap-media", 1280, 720, 3_000));
  const overlapOne = candidate([
    sharedEntry,
    entry("overlap-one-peer", version("overlap-media-one", 1920, 1080, 8_000)),
  ], { matched_identifier: "tt1234571" });
  const overlapTwo = candidate([
    sharedEntry,
    entry("overlap-two-peer", version("overlap-media-two", 1920, 1080, 8_000)),
  ], { matched_provider: "tmdb", matched_identifier: "500" });

  const result = analyzeSafeIdentityDuplicates([tied, overlapOne, overlapTwo]);

  assert.deepEqual(result.selected, []);
  assert.equal(result.skippedCandidates, 3);
});

test("survivor validation blocks selecting every copy in a matched candidate", () => {
  const group = candidate([
    entry("first", version("first-media", 1280, 720, 3_000)),
    entry("second", version("second-media", 1920, 1080, 8_000)),
  ]);
  const allKeys = new Set(group.entries.flatMap((item) => item.media_versions.map((media) => `${item.rating_key}:${media.media_id}`)));
  const oneKey = new Set(["first:first-media"]);

  assert.deepEqual(findCandidatesWithoutSurvivors([group], allKeys), [group]);
  assert.deepEqual(findCandidatesWithoutSurvivors([group], oneKey), []);
});

test("survivor validation also protects Plex versions attached to one entry", () => {
  const first = version("first-media", 1280, 720, 3_000);
  const second = version("second-media", 1920, 1080, 8_000);
  const group = { ...entry("same-entry", first), media_versions: [first, second] };
  const allKeys = new Set(group.media_versions.map((media) => `${group.rating_key}:${media.media_id}`));
  const oneKey = new Set(["same-entry:first-media"]);

  assert.deepEqual(findDuplicateGroupsWithoutSurvivors([group], allKeys), [group]);
  assert.deepEqual(findDuplicateGroupsWithoutSurvivors([group], oneKey), []);
});
