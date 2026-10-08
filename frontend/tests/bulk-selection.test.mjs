import assert from "node:assert/strict";
import test from "node:test";

import { analyzeLowerQualityVersions } from "../src/bulkSelection.ts";

function version(id, overrides = {}) {
  return {
    media_id: id,
    edition_title: null,
    duration_ms: 7_200_000,
    width: 1920,
    height: 1080,
    bitrate_kbps: 8_000,
    container: "mkv",
    video_codec: "h264",
    audio_codec: "aac",
    audio_channels: 2,
    file_size_bytes: 10_000_000,
    parts: [{ part_id: `part-${id}`, file: `/${id}.mkv`, size_bytes: 10_000_000, container: "mkv" }],
    streams: [
      { stream_type: "video", codec: "h264", language: null, channels: null, channel_layout: null, width: 1920, height: 1080, frame_rate: "24p", profile: "high", dynamic_range: "SDR" },
      { stream_type: "audio", codec: "aac", language: "eng", channels: 2, channel_layout: "stereo", width: null, height: null, frame_rate: null, profile: "LC", dynamic_range: null },
    ],
    ...overrides,
  };
}

function group(versions) {
  return {
    rating_key: "movie-1",
    library_id: "movies",
    library_title: "Movies",
    library_type: "movie",
    title: "Example Movie",
    year: 2020,
    parent_title: null,
    season_index: null,
    episode_index: null,
    poster_url: null,
    media_versions: versions,
  };
}

test("selects metadata-matching tied copies while preserving one version", () => {
  const keep = version("media-a");
  const extra = version("media-b");

  const result = analyzeLowerQualityVersions(group([keep, extra]));

  assert.deepEqual(result.selected.map((item) => item.version.media_id), ["media-b"]);
  assert.match(result.selected[0].reason, /technical details match/i);
  assert.equal(result.skippedReason, null);
});

test("selects every extra when three tied versions have matching technical details", () => {
  const result = analyzeLowerQualityVersions(group([
    version("keep"),
    version("extra-a"),
    version("extra-b"),
  ]));

  assert.deepEqual(result.selected.map((item) => item.version.media_id), ["extra-a", "extra-b"]);
  assert.equal(result.skippedReason, null);
});

test("selects tied copies using matching core file details when Plex omitted streams", () => {
  const result = analyzeLowerQualityVersions(group([
    version("keep", { streams: [] }),
    version("extra", { streams: [] }),
  ]));

  assert.deepEqual(result.selected.map((item) => item.version.media_id), ["extra"]);
  assert.match(result.selected[0].reason, /stream details are unavailable/i);
});

test("leaves tied versions for review when their audio or file details differ", () => {
  const keep = version("keep");
  const differentAudio = version("different-audio", {
    audio_codec: "ac3",
    streams: [
      keep.streams[0],
      { ...keep.streams[1], codec: "ac3", profile: "Dolby Digital" },
    ],
  });

  const result = analyzeLowerQualityVersions(group([keep, differentAudio]));

  assert.deepEqual(result.selected, []);
  assert.match(result.skippedReason ?? "", /technical details differ/i);
});

test("still selects a clearly lower-resolution version", () => {
  const best = version("best");
  const lower = version("lower", {
    width: 1280,
    height: 720,
    bitrate_kbps: 3_000,
    file_size_bytes: 5_000_000,
    parts: [{ part_id: "part-lower", file: "/lower.mkv", size_bytes: 5_000_000, container: "mkv" }],
    streams: [
      { ...best.streams[0], width: 1280, height: 720 },
      best.streams[1],
    ],
  });

  const result = analyzeLowerQualityVersions(group([lower, best]));

  assert.deepEqual(result.selected.map((item) => item.version.media_id), ["lower"]);
  assert.match(result.selected[0].reason, /lower resolution/i);
});
