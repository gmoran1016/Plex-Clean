import assert from "node:assert/strict";
import test from "node:test";

import * as authPolling from "../src/authPolling.ts";

test("rate-limited Plex polling starts with a minute of backoff", () => {
  assert.equal(authPolling.nextRateLimitedPollDelay(null, 0), 60_000);
});

test("rate-limited Plex polling honors Retry-After and caps exponential backoff", () => {
  assert.equal(authPolling.nextRateLimitedPollDelay(90, 0), 90_000);
  assert.equal(authPolling.nextRateLimitedPollDelay(null, 60_000), 120_000);
  assert.equal(authPolling.nextRateLimitedPollDelay(null, 240_000), 300_000);
});

test("recognizes Plex's forwarding return URL", () => {
  assert.equal(authPolling.isPlexAuthReturn?.("?plexAuthReturn=1"), true);
  assert.equal(authPolling.isPlexAuthReturn?.("?plexAuthReturn=0"), false);
  assert.equal(authPolling.isPlexAuthReturn?.(""), false);
});

test("each app tab polls a pending PIN", () => {
  assert.equal(authPolling.shouldPollPlexAuth?.(true), true);
  assert.equal(authPolling.shouldPollPlexAuth?.(false), false);
});
