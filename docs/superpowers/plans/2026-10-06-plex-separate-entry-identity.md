# Plex-Clean Separate-Entry Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The user selected Native execution in the current master checkout.

**Goal:** Find separately indexed Movies and TV episodes in the same Plex library when their supported external metadata IDs match, then let the user review and individually delete an exact media version.

**Architecture:** Extend the existing paginated scan with a per-library external-ID index and keep these candidates in a separate session result set. Add a separate frontend results mode; candidate entries retain their own Plex rating keys and reuse the existing freshness-checked single-version deletion request.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, unittest, React 18, TypeScript, Vite, Node test runner, Docker Compose.

**Spec:** docs/superpowers/specs/2026-10-06-plex-separate-entry-identity-design.md

## Global Constraints

- Compare items only within the currently selected library. Do not compare across library sections.
- Do not add a media-folder mount or direct filesystem access.
- Do not infer an identity from title, year, runtime, file name, or path alone.
- Keep explicit edition differences visible for review rather than treating them as safe duplicates.
- Deletion must target one exact Plex metadata entry and media version, with the existing freshness check and confirmation before Plex receives a delete request.
- Preserve the current Plex-version duplicate groups as a separate result set with unchanged meaning.
- Do not add candidate-wide deletion or automatic deletion in this feature.

## Review Focus

- Unsupported, malformed, missing, or query-decorated GUIDs must not crash scanning or become false matches; cover in Task 1 parser tests and Task 2 scan tests.
- A parent/show GUID alone must not match TV episodes; cover in Task 1 episode identity tests.
- Shared IDs must not merge across libraries or media types, and partially overlapping ID buckets must not merge transitively; cover in Task 1 grouping tests and Task 2 section-scope tests.
- Different or partially missing edition labels must remain review-only and outside the lower-quality bulk action; cover in Task 1 edition tests and Task 4 UI checks.
- A stale candidate member or changed Part path must never be deleted; cover in Task 3 freshness tests and Task 4 browser flow.

## File Map

- backend/app/models.py defines the public candidate and count response shapes.
- backend/app/plex/identity.py parses supported item-level provider IDs, groups exact identity buckets, and computes edition review state.
- backend/app/plex/library.py, backend/app/sessions.py, and backend/app/routes/library.py collect, retain, paginate, and expose scan results.
- backend/app/plex/deletion.py updates candidate snapshots while retaining the existing exact target and Part freshness checks.
- frontend/src/types.ts and frontend/src/api.ts define the client contracts and candidate endpoint.
- frontend/src/App.tsx, frontend/src/components/IdentityCandidateList.tsx, and frontend/src/components/IdentityCandidateCard.tsx provide the separate review mode.
- frontend/src/components/VersionCard.tsx and frontend/src/styles.css keep technical details and individual deletion clear while hiding bulk-selection controls in identity mode.

---

### Task 1: Provider IDs and candidate grouping

**Files:**
- Create: backend/app/plex/identity.py
- Create: backend/tests/test_identity_matching.py
- Modify: backend/app/models.py

**Interfaces:**
- `ExternalMediaId` contains `provider: Literal["imdb", "tmdb", "tvdb"]` and `identifier: str`.
- `IdentityCandidate` contains the library identity, one matched provider and identifier, `edition_review: Literal["clear", "conflict", "unknown"]`, and `entries: list[DuplicateGroup]`.
- `external_identity_ids(item: dict[str, Any]) -> tuple[ExternalMediaId, ...]` reads only the item’s `Guid[].id` values; it ignores the top-level `guid`, parent/show GUIDs, unsupported namespaces, and empty values.
- `build_identity_candidates(entries: Sequence[tuple[DuplicateGroup, tuple[ExternalMediaId, ...]]], library: LibrarySummary) -> list[IdentityCandidate]` creates exact provider-ID buckets for one library and filters members to that library and media type.
- Candidate buckets are not joined transitively. If multiple provider IDs produce the same member set, emit one candidate using provider priority IMDb, TMDb, then TVDB. Keep partial-overlap buckets separate.
- Edition state is clear when every member has the same edition label or every member is unlabelled; conflict when multiple explicit labels occur; unknown when only some members have a label.

- [ ] **Step 1: Write failing identity tests**

Add tests named `test_external_identity_ids_accepts_supported_item_guid_ids_only`, `test_external_identity_ids_normalizes_scheme_and_ignores_query_and_fragment`, `test_movie_entries_group_only_on_a_shared_external_id`, `test_episode_group_ignores_parent_and_show_guids`, `test_library_and_media_type_are_part_of_identity_scope`, `test_partial_id_buckets_do_not_merge_transitively`, and `test_edition_review_marks_conflicting_and_partial_labels`.

Assert IMDb/TMDb/TVDB IDs are accepted; Plex-native, parent/show, unknown, malformed, and blank IDs are ignored; identical member sets do not produce duplicate cards; and title/year similarity alone creates no candidate.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run from the repository root: `python -m unittest discover -s backend/tests -t backend -p test_identity_matching.py -v`

Expected: FAIL because the identity models and matching functions do not exist.

- [ ] **Step 3: Implement the public models and pure identity helpers**

Add the model fields and the three interfaces above in their specified files. Parse provider URIs without using a Plex record GUID as an external identity. Build candidates only from a single supplied library and retain each member’s full media-version list.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `python -m unittest discover -s backend/tests -t backend -p test_identity_matching.py -v`

Expected: PASS for supported/unsupported IDs, normalization, exact grouping, TV parent exclusion, non-transitive buckets, and edition review states.

- [ ] **Step 5: Commit Task 1**

Stage only backend/app/models.py, backend/app/plex/identity.py, and backend/tests/test_identity_matching.py. Commit as `feat: add Plex external-ID duplicate matching`.

### Task 2: Scan integration, session state, and API

**Files:**
- Create: backend/tests/test_identity_scan.py
- Modify: backend/app/plex/library.py
- Modify: backend/app/sessions.py
- Modify: backend/app/models.py
- Modify: backend/app/routes/library.py

**Interfaces:**
- Add `identity_candidate_count` and `missing_identity_id_count` to `LibrarySummary`.
- Add `identity_candidate_groups` and `items_without_supported_ids` to `ScanProgress`; keep `duplicate_groups` unchanged.
- Add session-scoped `identity_candidates: dict[str, list[IdentityCandidate]]`; clear it with the existing scan results on rescan, logout, and session expiry.
- Add `PlexLibraryService.get_identity_candidates(session: AppSession, library_id: str, offset: int, limit: int) -> list[IdentityCandidate]`.
- Add `GET /api/identity-candidates?library_id=...&offset=...&limit=...` with the same library validation and pagination bounds as `/api/duplicates`.
- Add `normalize_media_item(item: dict[str, Any], library: LibrarySummary, session: AppSession) -> DuplicateGroup | None`, which normalizes entries with one or more Media versions. Keep `normalize_group_item` limited to two or more versions by delegating to the shared normalizer.

- [ ] **Step 1: Write failing scan and route tests**

Add tests named `test_scan_builds_identity_candidates_from_paginated_item_guids`, `test_scan_counts_items_without_supported_ids`, `test_scan_does_not_change_plex_duplicate_group_count`, `test_same_provider_id_in_two_libraries_creates_separate_candidates`, `test_identity_candidate_route_paginates_and_rejects_unknown_library`, and `test_logout_clears_identity_candidate_results`.

Use a fake Plex client with multiple pages. Assert scan requests use the existing `includeGuids` field, no per-item metadata request is added, one-media entries can be candidate members, and current multi-version results keep their existing count.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python -m unittest discover -s backend/tests -t backend -p test_identity_scan.py -v`

Expected: FAIL because the scan state, candidate endpoint, and candidate counts do not exist.

- [ ] **Step 3: Integrate candidate indexing into the existing per-library scan**

Build provider-ID buckets from each paginated item, count items with no supported item-level provider ID, normalize candidate members, and store only candidate groups with at least two distinct rating keys. Update per-library and overall scan counts without changing the meaning of `duplicate_groups`.

- [ ] **Step 4: Expose session-scoped paginated results and clear them with scan state**

Implement `get_identity_candidates`, the read-only route, and reset/clear behavior. Preserve partial library scan failure behavior and keep Plex tokens/raw response objects out of candidate responses.

- [ ] **Step 5: Run focused scan tests and commit Task 2**

Run: `python -m unittest discover -s backend/tests -t backend -p test_identity_scan.py -v`

Expected: PASS for pagination, counts, route validation, and session clearing. Stage only the Task 2 files and commit as `feat: expose Plex identity duplicate candidates`.

### Task 3: Candidate-aware freshness-checked deletion

**Files:**
- Create: backend/tests/test_identity_deletion.py
- Modify: backend/app/plex/deletion.py
- Modify: backend/app/plex/library.py
- Modify: backend/app/sessions.py
- Modify: backend/app/models.py

**Interfaces:**
- Resolve a deletion snapshot by rating key from either an existing Plex-version group or an identity candidate entry.
- Fetch and normalize a current entry with one or more Media versions so a separate Plex metadata entry with one version can pass the same media-ID and Part-ID/path freshness check.
- After a confirmed delete, update the existing Plex-version groups and identity candidates that contain the entry. Remove a missing entry; remove a candidate once fewer than two members remain; recompute its edition review state when a member changes.
- Fetch fresh candidate metadata with item-level Guid values. On a stale Part/media mismatch, update or remove the candidate member only if its current external ID still matches the candidate key, return the stale conflict, and require a new user action.
- Return updated library counts through the existing `DeleteResult.library`. Do not add a candidate-wide or automatic delete endpoint.

- [ ] **Step 1: Write failing deletion tests**

Add tests named `test_identity_member_delete_checks_exact_media_and_parts`, `test_changed_identity_member_part_aborts_before_delete_and_refreshes_candidate`, `test_changed_identity_id_removes_member_from_candidate`, `test_deleting_identity_member_updates_or_removes_candidate`, and `test_existing_duplicate_group_delete_behavior_is_preserved`.

Assert a one-version candidate member can be deleted only after a fresh entry matches its scanned media ID and complete Part identities; a changed or missing Part prevents the Plex DELETE request; and successful deletion updates candidate and library counts.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python -m unittest discover -s backend/tests -t backend -p test_identity_deletion.py -v`

Expected: FAIL because deletion snapshots currently include only Plex multi-version groups.

- [ ] **Step 3: Extend deletion snapshot lookup and fresh-entry normalization**

Keep the existing server permission gate, exact Plex endpoint, timeout handling, and no-retry behavior. Use a one-or-more-version normalizer for freshness, then update both result sets from the same fresh PMS response.

- [ ] **Step 4: Run focused deletion tests and commit Task 3**

Run: `python -m unittest discover -s backend/tests -t backend -p test_identity_deletion.py -v`

Expected: PASS for exact target, stale Part rejection, candidate/count refresh, and existing duplicate deletion regression.

Stage only the Task 3 files and commit as `feat: safely delete reviewed Plex identity entries`.

### Task 4: Separate-entries review UI

**Files:**
- Create: frontend/src/components/IdentityCandidateList.tsx
- Create: frontend/src/components/IdentityCandidateCard.tsx
- Create: frontend/tests/identity-candidate-api.test.mjs
- Modify: frontend/src/types.ts
- Modify: frontend/src/api.ts
- Modify: frontend/src/App.tsx
- Modify: frontend/src/components/LibrarySidebar.tsx
- Modify: frontend/src/components/ScanStatus.tsx
- Modify: frontend/src/components/VersionCard.tsx
- Modify: frontend/src/styles.css
- Modify: frontend/package.json

**Interfaces:**
- Add TypeScript types mirroring `ExternalMediaId`, `IdentityCandidate`, and the separate library/scan counts.
- Add `api.getIdentityCandidates(libraryId: string, offset: number, limit?: number) -> Promise<IdentityCandidate[]>`.
- Add a `plex-versions` / `separate-entries` view state in `App.tsx`; fetch paginated candidate data only after the scan is complete and a library is selected. Show the existing lower-quality toolbar only in Plex versions mode.
- `IdentityCandidateCard` renders one candidate’s matched provider ID, edition review state, and distinct entry members. It calls the existing delete callback with each member `DuplicateGroup` and selected `MediaVersion`.
- Add an optional `showBulkSelection` prop to `VersionCard`, defaulting to true. Candidate member cards pass false, so identity candidates never enter the existing bulk selection map.

- [ ] **Step 1: Write the API test**

Add `test_get_identity_candidates_encodes_library_and_pagination` in frontend/tests/identity-candidate-api.test.mjs and include the test in the frontend npm test script. Assert the API calls the identity-candidate route with the requested library, offset, and limit and uses the shared same-origin request behavior.

- [ ] **Step 2: Run the focused frontend test and confirm it fails**

Run: `node --experimental-strip-types --test frontend/tests/identity-candidate-api.test.mjs`

Expected: FAIL because `api.getIdentityCandidates` does not exist.

- [ ] **Step 3: Add the candidate client contract and separate result mode**

Implement the new API/type contract, mode toggle, library candidate counts, scan counts, loading/empty/failed states, and pagination. Reset candidate view paging on library, server, logout, or scan changes.

- [ ] **Step 4: Render candidates and preserve individual deletion review**

Add the candidate list/card. Keep entries visibly separate, show the matched ID and review-only edition warnings, render all known media details and paths, hide bulk-selection checkboxes, and reuse the exact single-version DeleteConfirmation. Refresh the active identity candidate page and library counts after success or stale-result errors.

- [ ] **Step 5: Run frontend checks and commit Task 4**

Run: `node --experimental-strip-types --test frontend/tests/identity-candidate-api.test.mjs`

Expected: PASS.

Run: `npm run test --prefix frontend`

Expected: PASS for the full existing and new frontend test set.

Run: `npm run build --prefix frontend`

Expected: TypeScript and Vite build succeed.

Stage only the Task 4 files and commit as `feat: add separate Plex entry review mode`.

### Task 5: Full application verification

**Files:**
- No source changes unless a verification failure requires a scoped repair.

**Interfaces:**
- Validate backend, frontend, Docker packaging, and rendered UI against the approved spec.

- [ ] **Step 1: Run the complete backend unit suite**

Run: `python -m unittest discover -s backend/tests -t backend -v`

Expected: PASS, including all existing authentication coverage.

- [ ] **Step 2: Validate and build the Docker application**

Run: `docker compose config`

Expected: valid rendered Compose configuration.

Run: `docker compose build`

Expected: the updated image builds successfully.

- [ ] **Step 3: Start the updated app and verify the read-only Plex flow**

Restart the current Compose service on its existing port, confirm `/healthz`, sign in through the already configured Plex client, select the server, scan Movies and TV, inspect the new counts and both result modes, and verify any identity candidate lists its shared external ID and edition state. Do not confirm a media deletion during verification.

Expected: existing Plex-version results remain intact; the new mode shows same-library external-ID candidates or an accurate empty state; unsupported/missing IDs are counted; no candidate appears in the lower-quality selection toolbar.

- [ ] **Step 4: Commit any scoped verification repair**

If verification required a source repair, add only its source and focused test files, rerun the owning task’s checks and full build, then commit as `fix: resolve separate-entry verification issue`. Otherwise make no additional commit.
