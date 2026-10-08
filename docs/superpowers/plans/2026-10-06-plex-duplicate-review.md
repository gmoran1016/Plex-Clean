# Plex Duplicate Review App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Dockerized Plex duplicate-review web app for Movie and TV libraries, with Plex sign-in, version comparison, and confirmed deletion through Plex.

**Architecture:** A React/Vite frontend is compiled into static assets and served by a single-process FastAPI backend in one Docker image. The backend owns JWK-backed Plex PIN authentication, per-browser in-memory sessions, server discovery, paginated scans, and guarded deletion; Plex tokens and media paths are not persisted by the app.

**Tech Stack:** Python 3.12, FastAPI, HTTPX, React, TypeScript, Vite, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-06-plex-duplicate-review-design.md`

## Global Constraints

- Scan only Plex Movie and TV sections; enumerate TV episodes, not just shows.
- A duplicate group is one Plex metadata item with at least two `Media` versions; do not hash files or merge separate Plex metadata items.
- Delete one selected media version through Plex after re-fetching and checking its media ID and `Part` IDs/paths; never add bulk deletion or automatic keeper selection.
- Keep Plex user/server tokens in server memory only; never return or log them. Stop using the account token after 24 hours. Persist the stable Plex client identifier and mode-0600 Ed25519 signing key under `/config`; treat that volume as sensitive.
- Use one FastAPI worker because sessions and scan jobs are in memory. Expire sessions after 24 hours of inactivity; require Plex sign-in after container restart.
- Require the session CSRF token and same-origin validation on every state-changing route, including PIN start, logout, server selection, scan start, and deletion.
- Run automated and live tests when the user requests testing or verification. Build/configure the Docker image as the delivery check.
- No direct media filesystem mount is required; Plex performs deletion.

## Review Focus

These are manual review points mapped to the owning tasks; they do not add automated tests.

- Expired/unclaimed PIN and expired 24-hour app token produce a recoverable sign-in state — Task 2.
- Multiple servers, no reachable server, and Docker-only network failures are clearly handled — Task 2 and Task 8.
- Pagination, empty libraries, and episode-level TV scanning do not create false duplicate groups — Task 3.
- Missing fields, multi-part media, and runtime/edition differences remain visible and correctly scoped — Tasks 3 and 6.
- Disabled deletion, non-admin 403, stale media IDs/paths, and delete timeouts never trigger an automatic retry — Tasks 4 and 7.

---

### Task 1: Backend foundation, contracts, and sessions

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/config.py`
- Create: `backend/app/models.py`
- Create: `backend/app/sessions.py`
- Create: `backend/app/routes/__init__.py`
- Create: `backend/app/routes/session.py`

**Interfaces:**
- `Settings.from_environment() -> Settings` reads `PLEX_CONFIG_DIR`, optional `PLEX_SERVER_URL`, and `SESSION_COOKIE_SECURE`.
- `SessionStore.get_or_create(cookie_value: str | None) -> tuple[str, AppSession]`, `get(session_id: str) -> AppSession | None`, and `clear(session_id: str) -> None` manage 24-hour-idle in-memory sessions.
- `AppSession` owns pending PIN state, the in-memory Plex token, discovered/selected server access, CSRF token, scan state, and normalized duplicate results.
- `GET /healthz` returns process health. `GET /api/session` returns only signed-in state, selected server, CSRF token, and library/scan summaries; never tokens.

- [x] Create the Python package and runtime dependencies for FastAPI, HTTPX, and settings.
- [x] Implement environment configuration and stable load-or-create of the Plex client identifier and private device signing key in `/config`.
- [x] Define the shared Pydantic response models and the in-memory `AppSession`/`SessionStore` contracts.
- [x] Add opaque HttpOnly/SameSite session cookies, configurable Secure cookies, 24-hour idle expiry, and a reusable CSRF-token/same-origin dependency for state-changing routes.
- [x] Wire `/healthz` and `/api/session` into a single-process FastAPI app and ensure response models omit all token/key fields.

**Manual review:** Check that configuration defaults support local Docker use and that no secret-bearing property is serialized by session routes.

### Task 2: Plex authentication and server discovery

**Files:**
- Create: `backend/app/plex/__init__.py`
- Create: `backend/app/plex/auth.py`
- Create: `backend/app/plex/client.py`
- Create: `backend/app/routes/auth.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- `PlexAuthService.start_pin(session: AppSession) -> AuthStart`, `poll_pin(session: AppSession) -> AuthStatus`, and `refresh_if_needed(session: AppSession) -> None` own JWK-backed PIN/token state; expired tokens require sign-in again.
- `PlexResourceService.list_servers(session: AppSession) -> list[ServerOption]` returns reachable server names, machine identifiers, connection choices, and capability summaries without exposing access tokens.
- `POST /api/auth/pin`, `GET /api/auth/status`, and `POST /api/auth/logout` implement the browser flow. `GET /api/servers` lists choices; `PUT /api/servers/selection` accepts a machine identifier.
- `PlexClient.request(method: str, path: str, *, session: AppSession, params: dict | None = None) -> httpx.Response` applies the selected server connection and token server-side.

- [x] Implement JWK-backed Plex PIN creation, short-code Plex Link authorization, and signed device-JWT PIN polling for the account token.
- [x] Keep the returned account token only in the current server-side session and stop using it after 24 hours.
- [x] Discover Plex resources, obtain a legacy PMS device token when resources return a JWT, retry the canonical host on empty results, prefer reachable local/direct connections, and verify the configured server URL when no resources are advertised.
- [x] Add CSRF-protected auth and server-selection routes; return only an authorization URL, sign-in status, and safe server summaries to the frontend.
- [x] Handle PIN expiry, expired account sessions, unreachable resources, and Plex API errors without logging tokens or request authorization headers.

**Manual review:** Confirm the PIN, multi-server, no-reachable-server, and restart/re-authentication paths match the approved spec.

### Task 3: Library scan, duplicate grouping, and media details

**Files:**
- Create: `backend/app/plex/library.py`
- Create: `backend/app/routes/library.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- `PlexLibraryService.list_sections(session: AppSession) -> list[LibrarySummary]` returns only Movie and TV sections.
- `PlexLibraryService.start_scan(session: AppSession) -> ScanProgress` starts one session-scoped scan; `get_scan(session: AppSession, scan_id: str) -> ScanProgress` reports progress.
- `GET /api/libraries`, `POST /api/scan`, `GET /api/scan/{scan_id}`, and `GET /api/duplicates?library_id=...&offset=...&limit=...` expose safe scan data.
- `DuplicateGroup` contains Plex rating key, library/type/title/year and TV parent/index fields, poster, and two or more `MediaVersion` objects. Each `MediaVersion` includes `media_id`, stream attributes, and `PartInfo[]` with part ID, read-only path, and size.

- [x] Implement JSON requests and pagination for library sections, Movie items, and TV episodes.
- [x] Normalize `Media`/`Part` and stream fields into `MediaVersion`; retain nulls for absent Plex fields and sum Part sizes only when each Part size is available.
- [x] Group only one Plex metadata item with multiple `Media` versions; keep `Part` entries inside their parent version and exclude single-version items.
- [x] Add a CSRF-protected session-scoped scan job with per-library progress, partial-library error reporting, and in-memory results.
- [x] Add library, scan, and paginated duplicate routes; keep Plex-only identifiers needed for delete freshness checks on the backend.

**Manual review:** Inspect the normalized response shape for Movie, episode, missing-field, multi-Part, and empty-library cases against the approved Plex duplicate definition.

### Task 4: Guarded single-version Plex deletion

**Files:**
- Create: `backend/app/plex/deletion.py`
- Create: `backend/app/routes/deletion.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- `DeletionService.delete_version(session: AppSession, rating_key: str, media_id: str) -> DeleteResult` checks permission and freshness before calling Plex.
- `POST /api/duplicates/{rating_key}/media/{media_id}/delete` requires the session CSRF token and accepts identifiers only; it does not trust frontend-supplied paths or media properties.
- `DeleteResult` reports Plex success and the refreshed duplicate group/counts; a stale selection returns a conflict with current safe group data and performs no delete.

- [x] Read Plex server deletion capability and keep delete unavailable when `allowMediaDeletion` is false.
- [x] Re-fetch the metadata item immediately before delete and compare the selected media ID and all Part IDs/path(s) against the server-side scan snapshot.
- [x] Call only Plex's `DELETE /library/metadata/{ratingKey}/media/{mediaId}` endpoint for the exact selected media version.
- [x] Handle 401/403/404, connection timeout, and stale-result conflicts without automatic destructive retries; refresh the affected group only after a confirmed success or before presenting a retry.
- [x] Return no access token, signing key, or caller-provided filesystem path in delete responses.

**Manual review:** Walk through deletion-disabled, non-admin 403, changed Part path, multi-Part confirmation data, and timeout behavior; verify each failure aborts without retry.

### Task 5: Frontend foundation and Plex sign-in

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/styles.css`

**Interfaces:**
- `api.ts` functions `getSession()`, `startPlexLogin(csrfToken)`, `getAuthStatus()`, `logout(csrfToken)`, `getServers()`, and `selectServer(machineIdentifier, csrfToken)` call same-origin `/api` routes with `credentials: "same-origin"`.
- Frontend state is an in-memory React state tree. It never writes Plex credentials or server access tokens to localStorage/sessionStorage.

- [x] Initialize a React/TypeScript/Vite application and shared types corresponding to the backend contracts.
- [x] Implement one same-origin API client that includes the CSRF header for mutating requests and renders sanitized backend errors.
- [x] Build app states for loading, signed out, PIN pending, signed in/server selection, and selected server.
- [x] Add the Plex authorization link and poll auth status until success, expiry, or a recoverable error.
- [x] Keep selected server and auth status synchronized with `/api/session` after reload without persisting Plex secrets.

**Manual review:** Confirm browser state contains no Plex token, and auth/session expiry produces the expected screen transition.

### Task 6: Library navigation and visual duplicate comparison

**Files:**
- Create: `frontend/src/components/LibrarySidebar.tsx`
- Create: `frontend/src/components/ScanStatus.tsx`
- Create: `frontend/src/components/DuplicateList.tsx`
- Create: `frontend/src/components/DuplicateGroupCard.tsx`
- Create: `frontend/src/components/VersionCard.tsx`
- Create: `frontend/src/components/StreamDetails.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- `LibrarySidebar` consumes `LibrarySummary[]` and selects a `library_id`.
- `DuplicateGroupCard` consumes one `DuplicateGroup` and renders one `VersionCard` per `MediaVersion`.
- `ScanStatus` consumes `ScanProgress` and offers start/rescan actions.
- `api.ts` function `startScan(csrfToken: string) -> Promise<ScanProgress>` starts the session scan; scan-status requests are read-only.

- [x] Implement library counts and selection, scan/rescan controls, and polling for per-library scan progress.
- [x] Render results in library-grouped, paginated cards; show Movie/TV metadata and versions side by side.
- [x] Show resolution, bitrate, runtime, size, container, video/audio codecs, audio details, edition, and all associated paths when available.
- [x] Add expanded stream details and clearly label missing values as “Unavailable”.
- [x] Keep editions and duration deltas visible; leave all versions unselected and avoid a keeper recommendation.
- [x] Add loading, empty-library, no-duplicates, partial-scan, and server-error states with responsive layout.

**Manual review:** Inspect the UI at a narrow and wide viewport, especially long paths, missing values, TV context, and edition/runtime differences.

### Task 7: Deletion confirmation and safe UI refresh

**Files:**
- Create: `frontend/src/components/DeleteConfirmation.tsx`
- Modify: `frontend/src/components/VersionCard.tsx`
- Modify: `frontend/src/components/DuplicateGroupCard.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- `DeleteConfirmation` consumes a selected `MediaVersion` and group title/library; its confirm callback sends only `rating_key` and `media_id`.
- `api.ts` function `deleteVersion(ratingKey: string, mediaId: string, csrfToken: string) -> Promise<DeleteResult>` sends the CSRF-protected request.

- [x] Disable or explain delete controls when PMS reports deletion is unavailable.
- [x] Show an accessible confirmation dialog naming the selected version and listing every affected Part path; state that Plex deletes the source media.
- [x] Submit only one selection, display pending state, and prevent duplicate clicks while the request is in flight.
- [x] Render stale-selection, forbidden, not-found, timeout, and generic Plex errors without retrying automatically.
- [x] On success, replace the affected group and library counts using the backend response; if no duplicates remain, remove the group from the list.

**Manual review:** Verify keyboard-accessible confirmation, no bulk action, exact-path display, and no automatic retry after every delete error category.

### Task 8: Docker packaging, operating documentation, and delivery check

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `README.md`
- Modify: `backend/app/main.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Compose service exposes `${HOST_PORT:-8090}:8000`, mounts a named `/config` volume, and passes `PLEX_CONFIG_DIR=/config` plus optional `PLEX_SERVER_URL` and `SESSION_COOKIE_SECURE`.
- Docker image builds frontend assets in a Node stage, copies static output and the Python backend into the runtime image, and launches one Uvicorn worker as a non-root user.
- `GET /healthz` is the container healthcheck target.

- [x] Create a multi-stage Dockerfile that produces one image containing the built UI and FastAPI app.
- [x] Add Compose port, config volume, optional Plex connection override, and healthcheck settings.
- [x] Exclude Git state, local virtual environments, frontend dependencies, and runtime config from the Docker build context.
- [x] Document prerequisites, `docker compose up --build`, URL/port, Plex login, server choice, scans, delete behavior, logout, restart re-authentication, signing-key volume handling, and trusted-LAN/HTTPS guidance.
- [x] Run `docker compose config` and resolve configuration errors.
- [x] Run `docker compose build` and resolve image-build errors without adding or running a test suite.

**Manual review:** Confirm only `/config` persists, no media mount is present, port override works, and the Dockerfile runs one non-root backend worker.
