import assert from "node:assert/strict";
import test from "node:test";

const scanPolling = await import("../src/scanPolling.ts").catch(() => ({}));

test("terminal scan poll waits for refreshed library summaries before returning", async () => {
  assert.equal(typeof scanPolling.pollScanUpdate, "function");

  let releaseLibraries;
  const libraryRefresh = new Promise((resolve) => { releaseLibraries = resolve; });
  const scan = {
    scan_id: "scan-1",
    state: "complete",
    total_libraries: 1,
    libraries_complete: 1,
    current_library_id: null,
    current_library_title: null,
    items_scanned: 10,
    duplicate_groups: 2,
    errors: [],
  };
  const library = {
    library_id: "1",
    title: "Movies",
    type: "movie",
    item_count: 10,
    duplicate_count: 2,
    scan_state: "complete",
  };
  let pollSettled = false;
  const poll = scanPolling.pollScanUpdate(
    "scan-1",
    async () => scan,
    async () => libraryRefresh,
  ).then((result) => {
    pollSettled = true;
    return result;
  });

  await Promise.resolve();
  assert.equal(pollSettled, false);
  releaseLibraries([library]);

  assert.deepEqual(await poll, { scan, libraries: [library] });
});
