# Plex-Clean Duplicate Review App

**Date:** 2026-10-06

**Status:** Approved; implemented with the approved separate-entry bulk-selection extension below.

## Goal

Build a self-hosted web app in Docker that signs in to Plex, lists duplicate movie and TV versions by library, presents useful file and stream details for comparison, and lets the user delete one selected version through Plex.

## User and operating context

- The app is a single-user-oriented, self-hosted tool. Plex sign-in is the app's authentication; there is no separate app account.
- The user chooses among reachable Plex Media Servers if their Plex account exposes more than one.
- All Movies and TV libraries on the selected server are scanned. TV items are enumerated at episode level so version comparisons are useful.
- The app requires network access from its container to Plex.tv for authentication/discovery and to the selected Plex Media Server for library and delete operations.
- The app does not mount or access the media filesystem. Plex performs the selected media deletion.

## Product scope

### Included

- Plex PIN authentication using Plex's JWK-backed device JWT flow and short Plex Link code. Require a new sign-in after 24 hours.
- Discovery and selection of reachable Plex Media Servers associated with the signed-in account.
- Scanning of all Movie and TV libraries on the selected server, with progress reporting and a manual rescan action.
- A visual, library-grouped comparison of Plex items that have multiple media versions.
- Display of Plex-provided title, year, poster, TV show/season/episode context, edition, duration, resolution, bitrate, file size, container, video/audio codecs, audio channel details, stream details when available, and file path(s).
- Explicit, single-version-at-a-time deletion through Plex, followed by a refresh of the affected duplicate group.
- A Docker image and Docker Compose configuration with setup and operating instructions.

### Excluded

- Music, photo, and other non-video library types.
- Content hashing, file fingerprinting, filename heuristics, or matching separately indexed Plex metadata items to one another.
- Automatic selection of a keeper, auto-delete, or bulk deletion.
- A database or persistent storage of Plex user/server access tokens.
- Direct filesystem access or a media-volume mount inside the app container.

## Duplicate definition

Use Plex's own duplicate semantics: one Plex library metadata item with more than one `Media` version. Movie items are compared at the movie level; TV versions are compared at the episode level. `Part` entries belong to a media version and are displayed as its file path(s); they are not treated as separate versions.

The app will not claim that two versions are byte-identical. It will not group two distinct Plex metadata items solely because their title, year, episode number, or paths look alike. This follows Plex's user-facing duplicate filter, which describes duplicates as library items with more than one version. Plex's guidance for TV libraries switches the library view to episodes before applying that filter.

## Architecture

- **Frontend:** React and Vite. Build the frontend into static assets as part of a multi-stage Docker image.
- **Backend:** FastAPI serves the static frontend and same-origin JSON API. It owns all Plex authentication, server requests, scan state, and delete operations.
- **Deployment:** One Docker service/image and one backend process. A single process is required because active Plex sessions and scan jobs are held in memory.
- **Persistence:** Keep the stable Plex client identifier and Ed25519 device signing key in the `/config` volume so the JWK identity remains stable across restarts. Store the private key in a mode-0600 file. After restart, the user signs in again.
- **Secrets:** Keep Plex user/server tokens only in server memory, scoped to the browser session. Never place tokens or the private key in browser storage, frontend responses, or logs. Treat the `/config` volume as sensitive because it contains the private signing key.
- **Session:** Use a random opaque session cookie marked `HttpOnly` and `SameSite=Lax`; mark it `Secure` when HTTPS is used. Apply same-origin/CSRF protection to state-changing requests. Clear server-side token material on logout or session expiry.
- **Network:** Prefer Plex-provided local/direct server connections when reachable. If Plex returns no server resources, allow the optional configured server URL only after its identity and authenticated root are verified with the signed-in account token. If no connection works from Docker, show a clear network/configuration error. Relay is a last resort and should be identified as such if used.
- **Media access:** No media directory is mounted into the app. All file deletion is requested from PMS using its media identifier.

## Authentication and server discovery

1. The browser starts sign-in through the app backend.
2. The backend creates a Plex PIN using its stable client identifier and an Ed25519 public JWK, then gives the browser the Plex authorization URL.
3. The browser completes authorization at Plex. The backend checks the PIN and obtains a JWT without returning it to the browser.
4. The backend obtains the signed-in account's available Plex resources and connection URLs. If a resource token is a JWT, request a legacy PMS token through Plex's device endpoint; verify the selected connection's identity and authenticated root before accepting it. If the client resource host returns an empty set, retry the canonical Plex host; if both have no server entry, verify the configured server URL directly and accept it only if its authenticated root succeeds.
5. If multiple reachable servers are available, the UI asks the user to select one. Otherwise the sole reachable server is selected automatically.
6. All subsequent Plex Media Server requests are made by the backend with the selected server access token, or the signed-in account token for a verified configured-server fallback. The backend stops using the account token after 24 hours and requests a new sign-in.

If sign-in expires or a Plex request reports an invalid token, stop the affected operation and request sign-in again. Do not log authentication headers or PIN secrets.

## Library scan and result model

- List the selected server's library sections and include sections whose types are Movie or TV.
- Fetch library items using Plex JSON responses and pagination. For TV sections, fetch episodes rather than only show-level containers.
- For each metadata item, inspect its `Media` versions and their `Part` entries. Keep only items with at least two versions.
- Track per-library totals, duplicate-group count, and scan progress. Scans and results are session-scoped and in memory.
- Keep the raw Plex identifiers needed for a later freshness check, but send the frontend only the fields required to render the UI.
- Treat fields as optional. If PMS does not provide a field, show “Unavailable” instead of inferring a value.

## Comparison interface

- Show a server selector/status, a scan/rescan action, scan progress, and a navigation list of Movie and TV libraries with duplicate counts.
- Group cards by selected library. Each group shows the Movie title/year or TV show, season, episode number/title, poster where available, and each media version side by side.
- Make resolution, bitrate, runtime, size, container, and codecs visible in the comparison. Show stream-level details and file path(s) in an expanded detail area.
- Show edition labels and duration differences plainly so extended cuts, alternate edits, and other meaningful versions remain distinguishable.
- Do not preselect a version for deletion or present a quality heuristic as a safe deletion recommendation.
- Mark missing or unknown metadata as unavailable. Keep file paths read-only.

## Deletion flow

1. Show deletion controls only when the selected PMS reports that media deletion is enabled. The server may still reject a non-admin account; explain the server-admin requirement and handle authorization errors clearly.
2. The user chooses one version and clicks Delete. There is no bulk-delete action.
3. Before deletion, the backend re-fetches the metadata item and verifies that the selected media ID and its part IDs/path(s) still match the scanned result. If the selection is stale, abort and refresh the group.
4. Show a confirmation naming the library and title, the selected version's details, and every path that PMS associates with that version. State that Plex will delete the source media. If a version contains multiple parts, make clear that all listed parts are affected.
5. On confirmation, call Plex's `DELETE /library/metadata/{ids}/media/{mediaItem}` endpoint for that exact media version.
6. On success, re-fetch the group and update library counts. Report Plex's response; do not claim an independent filesystem verification because the app has no filesystem access.

Disable deletion when the server reports it is unavailable. Handle PMS 401/403/404 and connection failures without retrying a destructive request automatically. A second explicit user action is required after any failed/stale operation.

## Error handling

- Expired or unclaimed PIN: allow the user to restart sign-in and generate a new PIN.
- No reachable Plex server: explain that the container must be able to reach the selected server; provide the optional server URL override guidance.
- Library scan failure: preserve completed library results, identify the failed library, and allow retry.
- Missing metadata: render as unavailable without dropping the duplicate group.
- Deletion disabled or insufficient account permission: keep files visible, disable or reject deletion, and explain the PMS setting/admin requirement.
- Stale media version: do not delete; refresh the group and ask the user to choose again.
- PMS delete error or timeout: show the server response category and refresh the group before the user retries. Never blindly repeat a DELETE request after a timeout.

## Docker and operation

- Include `Dockerfile`, `compose.yaml`, `.dockerignore`, and a README with required network access, configuration, sign-in, scan, delete, logout, and restart behavior.
- Expose the web UI/API on a configurable host port, defaulting to 8090.
- Mount `/config` for the client identifier and private device signing key. Do not require a Plex media path mount.
- Run the container as a non-root user and include a lightweight health endpoint for Docker health reporting.
- Document that public/WAN access should be provided through HTTPS; keep the default setup suitable for a trusted home network.
- The delivery check is a Docker image build and configuration inspection. No test suite will be added or run unless the user requests testing or verification.

## Acceptance criteria

1. A user can sign in through Plex PIN authentication without entering or exposing a Plex token in the browser.
2. The user can choose an accessible Plex server and scan its Movie and TV libraries.
3. The app lists every item in the scanned libraries that has multiple Plex media versions, grouped by library.
4. Each listed version shows the available requested file properties and its path(s), with absent values clearly marked.
5. Edition and runtime differences remain visible, and no version is preselected for deletion.
6. A user with server deletion rights can confirm deletion of one selected version; the backend freshness-checks it and the UI refreshes after Plex responds.
7. Users without server deletion permission receive an explanation and cannot accidentally submit an unreviewed or stale deletion.
8. `docker compose up --build` builds and starts the app with the documented port and configuration.

## Approved follow-up: separate-entry bulk selection (2026-10-07)

This approved extension supersedes the earlier exclusion of bulk deletion above, while preserving an explicit review and confirmation for every batch.

- In Separate Plex Entries mode, users can select individual media versions and request a library-wide safe selection.
- Automatic selection keeps one unique best-quality copy in each candidate with clear edition metadata, comparable runtimes, known resolution, and an unambiguous quality winner by resolution then bitrate. It skips edition/runtime/quality conflicts, entries with multiple versions, incomplete target file identifiers, and candidates that overlap another external-ID group.
- Before opening the confirmation, fetch and validate the complete current result set and reject any selection that would remove every media version from a matched candidate or Plex-version group.
- The confirmation lists every selected title, matched external ID when applicable, technical details, and file path. On confirmation, issue the Plex delete requests sequentially, revalidate each exact scan/server/library/media/part/path and reviewed identity, and stop after the first stale result or error.
- No media is deleted by selection alone; one final user confirmation is required for the batch.

## Source references

- Plex Media Server API, including PIN/JWT authentication, server resources, media fields, and the single-media delete endpoint: <https://developer.plex.tv/pms/>
- Plex API forum report describing PMS rejection of JWT resource tokens and the `/api/v2/devices` legacy-token route: <https://forums.plex.tv/t/question-on-https-clients-plex-tv-api-v2-resources-and-jwt-authentication/934478>
- Plex duplicate/merged content guidance: <https://support.plex.tv/articles/202393718-how-do-i-find-duplicate-or-merged-content/>
- Plex library view and deletion requirements: <https://support.plex.tv/articles/200392126-using-the-library-view/>
