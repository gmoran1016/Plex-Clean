# Plex-Clean Separate-Entry Duplicate Detection

**Date:** 2026-10-06

**Status:** Design approved in chat; written specification awaiting user review.

## Goal

Add a distinct review mode that finds likely duplicate Movies or TV episodes represented by separate Plex metadata entries in the same library. Preserve the existing Plex-version duplicate scan and its lower-quality selection behavior.

## User and operating constraints

- Compare items only within the currently selected library. Do not compare across library sections.
- Plex user/server tokens remain server-side and session-scoped.
- Do not add a media-folder mount or direct filesystem access.
- Do not infer an identity from title, year, runtime, file name, or path alone.
- Keep explicit edition differences visible for review rather than treating them as safe duplicates.
- Deletion must target one exact Plex metadata entry and media version, with the existing freshness check and confirmation before Plex receives a delete request.

## Approaches considered

1. **Exact shared external metadata IDs (selected).** Match separate entries in one library when they share a supported provider ID and media type. This is explainable and avoids title-only guesses, but misses items without usable IDs.
2. **Metadata heuristics.** Compare normalized titles, years, episode numbers, and runtimes. This finds entries whose IDs disagree or are missing, but risks combining remakes, alternate episode orders, and different cuts. It is out of scope for this change.
3. **Content fingerprints.** Compare media bytes or sampled audio/video. This can find re-encodes, but needs media-file access and additional processing. It is out of scope for this change.

## Identity rules

- Read the external IDs Plex returns in each item's Guid[].id entries during the existing paginated library scan. Do not use the item's top-level guid or parent/show GUID fields as identity keys.
- Compare external IDs after safe, deterministic normalization. Accept the imdb, tmdb, and tvdb provider namespaces; exclude unknown namespaces. Normalize the provider scheme case-insensitively and ignore query/fragment metadata while preserving the provider ID value. The media type and library section must also match.
- A Movie candidate requires at least two distinct Plex rating keys in the same Movie library with a common supported external Movie ID.
- A TV episode candidate requires at least two distinct Plex rating keys in the same TV library with a common supported episode-level ID. A series ID alone is insufficient; do not infer identity from season and episode numbers when episode IDs are absent.
- Do not create candidates from title/year/runtime/path similarity when no shared supported external ID exists.
- Keep records with different explicit edition labels, or an edition label present on only some members, as review-only identity candidates. Show the edition values and a clear conflict/unknown warning; do not describe them as safe duplicate copies or include them in lower-quality bulk selection. If all members have the same edition label or all are unlabelled, no edition conflict is inferred.
- If required identity data is missing, omit the candidate rather than guessing. Report counts for items skipped for missing IDs so the user can distinguish “no matches” from unavailable metadata.

## Scan and backend data flow

- Extend the existing scan to build a per-library index of the already fetched item GUIDs. Do not make an extra Plex request per item.
- Preserve the current Plex-version duplicate groups as a separate result set with unchanged meaning.
- Add a separate session-scoped identity-candidate result set, rebuilt on every scan and cleared on logout/session expiry.
- Each candidate contains its library identity, matched provider and identifier, a “metadata ID match” reason (not proof that file bytes are identical), an edition review state, and two or more distinct Plex entries.
- Each entry retains its own rating key, title, year, TV context, poster reference, edition label, and all normalized media versions/parts needed to render details and perform a later freshness check.
- Keep raw PMS fields and Plex tokens on the backend. Return only the safe identity and media fields needed by the review UI.
- Add separate identity-candidate and missing-supported-ID counts to library/scan progress without changing the meaning of the existing Plex-version duplicate count.

## Review interface

- Add a separate “Separate Plex entries” result mode for the selected library. Do not merge these candidates into the existing multi-version cards.
- Show each Plex entry as a distinct member, including title/year or show/season/episode context, matched provider ID, poster, edition, quality and codec details, runtime, size, container, streams, and paths when Plex provides them.
- Explain why the entries were grouped. Render missing metadata as unavailable.
- Mark edition conflicts prominently as review-only. Do not select any entry by default or infer which copy should be kept.
- Keep existing lower-quality selection limited to the existing multi-version groups. Identity candidates with edition conflicts are never eligible for that action.
- A user may request deletion of one specific media version belonging to one candidate member. Show that entry’s title, library, technical details, and every associated path in the existing confirmation flow.

## Deletion and stale data

- Reuse the existing Plex deletion path for an exact member rating key and media ID.
- Before deletion, refetch the entry and verify the selected media ID and all associated part IDs/paths against the scan snapshot.
- If the entry or media version changed, abort without deleting, refresh the candidate, and require a new user action.
- Do not add candidate-wide deletion or automatic deletion in this feature.
- On confirmed deletion, refresh the affected candidate and the selected library’s Plex-version and identity-candidate counts. Report Plex’s result without claiming independent filesystem verification.

## Errors and edge cases

- Missing GUIDs: omit from matched candidates and report the missing-ID count.
- Conflicting or partially missing edition labels: retain as a clearly warned review candidate, not a safe duplicate recommendation.
- Repeated metadata GUIDs on three or more distinct entries: render one candidate with all entries and allow only individual version deletion.
- Same ID across different libraries: do not group; the candidate remains scoped to its library.
- Same title with different provider IDs: do not group automatically.
- Plex authentication, stale-entry, permission, and deletion errors follow the current app behavior; never retry a destructive request automatically.

## Acceptance criteria

1. Two separate Movie entries with a shared supported external Movie ID in the same library appear together as one identity candidate.
2. Two separate episode entries with a shared supported episode ID in the same TV library appear together; a shared series ID alone does not create a match.
3. Items from different libraries, different media types, or without shared supported external IDs are not grouped.
4. Explicit edition conflicts are clearly marked for review and never become lower-quality bulk selections.
5. The current Plex-version duplicate list and its existing behavior remain distinct and unchanged.
6. Candidate entries retain their own rating keys and media details; deleting one version uses the existing exact-target confirmation and freshness check.
7. The UI explains missing-ID counts and shows available metadata without guessing absent values.
8. No media-directory mount, token persistence, candidate-wide deletion, or automatic deletion is introduced.

## Source references

- Plex Media Server API: https://developer.plex.tv/pms/
- Plex guidance distinguishing versions from editions: https://support.plex.tv/articles/multiple-editions/
- Plex library view and duplicate filter: https://support.plex.tv/articles/200392126-using-the-library-view/
- Existing Plex-Clean design: docs/superpowers/specs/2026-10-06-plex-duplicate-review-design.md
