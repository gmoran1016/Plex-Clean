# Remembered Plex sign-in design

## Intent

Make Plex-Clean's sign-in feel like a direct Plex sign-in: users open Plex's own approval screen from a button, do not type a PIN into Plex Link, and do not repeat authorization just because Plex-Clean or the browser restarts.

Today Plex-Clean creates a short PIN, asks the user to visit `plex.tv/link` and enter it, and keeps the resulting Plex token only in the in-memory browser session. The app's 24-hour session limit and a container restart both return the user to sign-in. The existing `/config` volume already retains the stable Plex client identifier and Ed25519 device key.

## Goals

- Start Plex's supported PIN authorization flow from a **Sign in with Plex** button. Open the Plex Auth App in a popup/new tab and continue polling in the Plex-Clean tab. Do not ask users to type a code or expose Plex passwords to Plex-Clean.
- Persist each browser's Plex account credential on the server, associated with a one-way hash of that browser's existing random session ID. Restore it after the browser or container restarts.
- Refresh Plex's expiring JWT with Plex-Clean's existing Ed25519 device key so a valid authorization can outlive a container restart and ordinary token expiration.
- Keep the Plex token out of JavaScript storage, URLs, and logs. Restrict persisted credential files to the app's OS account (`0600`) under the already sensitive `/config` volume, and write them atomically.
- Make sign-out clear the current browser's saved credential and in-memory state. It must not affect other browser sessions.
- If Plex has revoked or rejected the saved credential, remove it and return the browser to sign-in. A temporary network or rate-limit failure must retain it so the user can retry.
- After a restart, rediscover Plex servers and libraries from Plex. Scans and review state remain in memory and are not restored.

## Non-goals and anti-goals

- No Plex username/password form, Plex password handling, or credential in browser storage.
- No shared, instance-wide Plex account that silently signs every browser in as the same user.
- No automatic scanning or deletion after sign-in. The current server choice and explicit delete confirmation remain in place.
- No persistence of scan results, server access tokens, or other session data beyond the Plex account credential.
- No revocation of the Plex-Clean device from Plex on local sign-out; sign-out removes Plex-Clean's copy. Device revocation remains available in Plex account settings.

## Approaches

1. **Direct Plex Auth popup with per-browser remembered authorization (selected).** Request a strong PIN, build a Plex Auth App URL using the stable client identifier and PIN code, and open it from the sign-in button. The main tab keeps using the existing PIN status polling. Persist and refresh the account token per browser session in `/config`; clear it on sign-out or confirmed revocation. This removes manual code entry while respecting the app's current per-browser session model.
2. **Direct Plex Auth popup with one shared remembered account.** The same popup, but save one token for the entire installation. This is simpler but makes every browser use the same Plex identity and makes one browser's sign-out affect all users.
3. **Keep Plex Link and remember the credential per browser.** This adds durable auth and refresh but retains the manual linking/code entry that prompted the request.

The user selected the per-browser approach. It is the closest match for Seerr-style sign-in and keeps credentials isolated between browser sessions.

## Constraints and security

- Use Plex's documented PIN/Auth App flow. A strong PIN is required for the Auth App URL; a four-digit PIN belongs on the manual Plex Link page.
- Follow Plex's device JWT refresh flow, using the persistent Ed25519 key already registered for this Plex-Clean instance. Do not keep using a token past its actual Plex expiration.
- Persist only the account token, its expiration, and the account display name. Re-discover server connection tokens after restoration; do not persist server access tokens.
- Key saved records by a hash of the session cookie, never by the raw cookie value. Keep the browser cookie `HttpOnly`, `SameSite=Lax`, and subject to the existing `SESSION_COOKIE_SECURE` setting. Make it persistent so closing and reopening the browser does not discard the per-browser identity; renew it while active and expire abandoned records after the same retention period.
- Keep non-auth session data subject to the existing in-memory idle timeout. If the app process restarts or an idle session is evicted, recreate the session from its saved Plex credential only; libraries and scans are reloaded.
- Treat `/config` as sensitive, as the README already instructs. File permissions protect credentials from other OS users in the container; anyone with access to the whole config volume can access the signing key and credential records.
- Preserve the existing CSRF and origin checks on state-changing authentication requests.

## User-visible behavior

- The sign-in screen has one primary **Sign in with Plex** action. Clicking it opens a blank popup synchronously, then directs that popup to the Plex Auth App once the backend has created a PIN. If the browser blocks the popup, show a normal **Continue to Plex** link to the same URL.
- While Plex approval is pending, explain that Plex-Clean is waiting and keep the main page open. Do not display a code or instruct the user to enter it at Plex Link.
- On approval, close the popup when possible and continue to server selection. On return visits in the same browser, skip sign-in and discover servers automatically.
- **Sign out** removes that browser's saved Plex credential and ends its session. Another browser remains signed in.
- If the browser cookie is cleared/expired, the config volume is removed, or Plex revokes the credential/device key, that browser must sign in again.

## Builder calls

- Use a one-year rolling persistent cookie for browser continuity. The user only specified persistence across container restarts; the long-lived `HttpOnly` cookie also makes the same browser survive normal browser restarts. An idle record older than the cookie retention is cleaned up.
- Keep authentication records in the existing config volume with restrictive file permissions and atomic replacement. Do not add an external database or key-management service.
- Continue polling in the main tab rather than relying on `forwardUrl`; this avoids requiring Plex-Clean's public base URL or proxy-forwarding configuration.
- If Plex returns a 401/expired-token response, refresh once before clearing browser data. Clear saved auth only on a definitive refresh rejection, not on network errors or rate limits.

## Acceptance criteria

- A user can sign in through Plex's Auth App without typing a PIN into `plex.tv/link`.
- The frontend never receives the Plex account token and never writes it to browser storage.
- After sign-in, the same browser remains authenticated after a container restart and browser relaunch; the app restores the Plex token, refreshes it when required, and rediscovers servers.
- Sign-out clears only the current browser's saved credential. A revoked credential returns that browser to sign-in; a temporary Plex outage does not erase it.
- The existing manual-login experience is replaced by the direct approval flow, and product/README security descriptions match the new credential persistence behavior.
