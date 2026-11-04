# Remembered Plex sign-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual Plex Link code entry with a direct Plex approval window and remember each browser's Plex authorization across app and browser restarts.

**Architecture:** Keep PIN creation and polling on the FastAPI server, but open Plex's Auth App using the returned strong PIN. Persist only the Plex account JWT per browser session in `/config`, restore it from that browser's persistent HttpOnly cookie, and refresh it with the existing Ed25519 key. Keep server discovery and all scan state ephemeral.

**Tech Stack:** FastAPI, Python, `httpx`, `cryptography`, React 19, TypeScript, Vite.

**Spec:** `docs/superpowers/specs/2026-11-04-plex-remembered-sign-in-design.md`

## Global Constraints

- Per-browser credentials stay server-side and live under the existing `/config` volume with file mode `0600` and atomic replacement.
- Associate a saved credential with a SHA-256 hash of the browser's random session ID; never place raw Plex tokens in the browser, URLs, logs, or filenames.
- Keep the cookie `HttpOnly`, `SameSite=Lax`, controlled by `SESSION_COOKIE_SECURE`, and persistent with a one-year rolling lifetime.
- Use Plex's strong PIN Auth App flow and refresh its seven-day JWT with the existing Ed25519 device key.
- Sign-out clears only the current browser's saved credential. Scan state, selected server tokens, and review data remain in memory.
- Preserve current CSRF and same-origin checks. Do not add or run tests for this change unless the user requests verification.

## Review Focus

- A blocked popup must leave a usable direct Plex link and keep polling active in the original page.
- A container restart or in-memory idle eviction must restore only the matching browser's Plex token, not its scan state.
- A token near or past its seven-day expiry must refresh through the nonce and device JWT flow before Plex rejects normal API calls.
- A definitive token/device rejection must clear that browser's saved auth; a 429, network failure, or other temporary Plex outage must preserve it.
- A Plex Media Server connection-token 401 must not delete the still-valid account authorization; refresh server discovery without replaying the failed operation.
- Active API use must keep a credential record's retention timestamp aligned with the rolling browser cookie without rewriting the token file on every request.
- Logging out in one browser must not remove another browser's credential, and a crafted cookie must not create an unsafe credential-file path.

Review these cases manually from code and the resulting sign-in flow; no test files are added and no test commands are run under the current instruction.

---

### Task 1: Per-browser credential persistence and session restoration

**Files:**
- Create: `backend/app/auth_storage.py`
- Modify: `backend/app/sessions.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- `SavedPlexCredential` contains `token: str`, `expires_at: float | None`, `username: str | None`, and `saved_at: float`.
- `PlexCredentialStore(config_dir: Path)` exposes `load(session_id: str) -> SavedPlexCredential | None`, `save(session_id: str, credential: SavedPlexCredential) -> None`, `delete(session_id: str) -> None`, and `prune(now: float) -> None`.
- `SessionStore(idle_timeout_seconds: int, credentials: PlexCredentialStore | None = None)` restores only the account token/name/expiry when a known cookie's in-memory session is gone; scans and server access remain empty. Keep the credential store optional for existing in-memory callers.

- [x] Create `PlexCredentialStore` in `backend/app/auth_storage.py`. Name records with `sha256(session_id).hexdigest()`, validate session IDs before use, write JSON to a temporary file with mode `0600`, flush it, then atomically replace the target. Prune records older than 365 days.
- [x] Extend `AppSession`/`SessionStore` in `backend/app/sessions.py` to restore a saved account credential for the same browser cookie after idle eviction or process restart, rotate unknown cookies with no matching credential, and track a token fingerprint so unchanged credentials are not written on every request. Refresh the retention timestamp at most once per day while the browser remains active. Keep server-state clearing separate from account-auth clearing so a rejected server token does not remove the saved Plex account authorization.
- [x] Update `backend/app/main.py` to create the credential store from `Settings.config_dir`, share it with `SessionStore`, expose the session ID on `request.state`, persist changed authenticated credentials after API responses, and issue the same `HttpOnly`, `SameSite=Lax`, Secure-configured cookie with a rolling `31536000` second lifetime.
- [x] Manually inspect storage paths and session transitions for the malformed-cookie, restore-after-restart, and per-browser isolation cases in Review Focus.

### Task 2: Direct Plex approval and JWT refresh

**Files:**
- Modify: `backend/app/plex/auth.py`
- Modify: `backend/app/plex/client.py`
- Modify: `backend/app/routes/auth.py`
- Modify: `backend/app/routes/deletion.py`
- Modify: `backend/app/routes/library.py`
- Modify: `backend/tests/test_auth_rate_limit.py` (update the existing PIN-flow assertion; add no tests)

**Interfaces:**
- Keep `PlexAuthService.start_pin(session: AppSession) -> AuthStart` and `poll_pin(session: AppSession) -> AuthStatus`.
- Keep `PlexAuthService.refresh_if_needed(session: AppSession) -> None`; make it refresh an expired or near-expiry account JWT and retain existing network/rate-limit errors.

- [x] Change `start_pin` to request `strong: true` alongside the existing JWK and return an Auth App URL containing `clientID`, `code`, and Plex-Clean device context instead of `https://plex.tv/link`.
- [x] Add `PlexAuthService._signed_device_jwt(nonce: str | None = None) -> str`; on refresh include Plex's nonce and the `username` scope, then call `GET /api/v2/auth/nonce` and `POST /api/v2/auth/token`. Read JWT expiry when available, refresh within 24 hours of expiry, and update the in-memory token/expiry without changing username.
- [x] Preserve the HTTP status for Plex credential-rejection responses so a definitive 401/403/498 invalidates the session while rate limits, network failures, and server errors do not.
- [x] On an account API 498 or a 401 after the existing canonical-host fallback, refresh once and retry before clearing the browser's saved authorization.
- [x] When a Plex Media Server rejects its server connection token, clear server/scan state and refresh server discovery while retaining the browser's saved account authorization. Do not retry the rejected operation.
- [x] Pass `request.app.state.plex_device_private_key` to the `PlexAuthService` instances in `backend/app/routes/deletion.py` and `backend/app/routes/library.py`; every route must refresh with the same key registered during PIN sign-in.
- [x] Update `backend/app/routes/auth.py` so sign-out deletes the credential record identified by `request.state.app_session_id` before clearing the current in-memory session; other session IDs remain untouched.
- [x] Review the code against the rejection-versus-outage cases in Review Focus; do not add or run tests unless requested.

### Task 3: Popup sign-in experience

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/api.ts` only if the response shape changes

**Interfaces:**
- Continue using `api.startPlexLogin(csrfToken)`, `api.getAuthStatus()`, and the existing `AuthStart.authorization_url`.
- Do not add a client-side Plex token or PIN-polling implementation.

- [x] Update the sign-in click handler to synchronously open a blank popup, request the PIN, and navigate the popup to the returned Auth App URL. If the popup is blocked, expose a **Continue to Plex** link to that same URL.
- [x] Replace manual PIN instructions/code display with clear waiting/approval copy, keep the existing backend polling and rate-limit backoff, and close the popup when sign-in completes when browser policy permits.
- [x] When `api.getSession()` returns an authenticated restored session, continue directly to server discovery; do not persist scan results or display the linking screen. After a server-token rejection, rediscover servers without asking the user to approve Plex again.
- [x] Review popup-blocked and approval-completed states at desktop and narrow widths using existing styles; do not add or run tests unless requested.

### Task 4: Product and operator documentation

**Files:**
- Modify: `PRODUCT.md`
- Modify: `README.md`

- [x] Replace the 24-hour in-memory-only credential description with per-browser saved account credentials in the sensitive config volume, JWT refresh behavior, browser cookie behavior, and local sign-out semantics.
- [x] Update the quick-start sign-in instructions to describe the Plex Auth App approval window and remove instructions to enter a code at Plex Link.
- [x] Confirm product language still says scans and server connection tokens are session-scoped and not persisted.
