# Plex-Clean

A self-hosted Plex library review tool. Sign in to Plex to scan Movie and TV libraries, compare multiple media versions on one Plex item, and review separate Plex items with a shared IMDb, TMDb, or TVDb ID before requesting deletion through Plex.

## Start with Docker Compose

Requirements: Docker Engine with the Compose plugin, and network access from the container to `clients.plex.tv` and your Plex Media Server.

```powershell
docker compose up --build -d
```

Open [http://localhost:8090](http://localhost:8090). To use another host port, create a `.env` file beside `compose.yaml`:

```dotenv
HOST_PORT=8091
```

## Install with Unraid Docker

The container image is published to `ghcr.io/gmoran1016/plex-clean:latest`. In Unraid, open **Docker → Add Container** and use these settings:

- **Name:** `plex-clean`
- **Repository:** `ghcr.io/gmoran1016/plex-clean:latest`
- **Network Type:** `Bridge`
- **Port:** host `8090` to container `8000` over TCP
- **Path:** `/mnt/user/appdata/plex-clean` to `/config` (read/write)
- **Extra Parameters:** `--user=99:100 --restart=unless-stopped --security-opt=no-new-privileges:true --cap-drop=ALL`

The config folder should be owned by Unraid's `nobody` user and kept private; UID/GID `99:100` matches that folder while keeping the container non-root.

Do not add a Plex media path. Plex-Clean reviews metadata and submits confirmed deletion requests through Plex; it does not need direct access to media files. Keep the web interface on a trusted home network, or protect it with HTTPS and access controls before routing it through a reverse proxy.

After the container starts, open `http://<unraid-ip>:8090`. Sign in with Plex and select the server. If Plex advertises a server address the container cannot reach, set `PLEX_SERVER_URL` to the reachable Plex URL in the container environment and restart it.

The app listens on the configured host port. The named `plex_clean_config` volume mounted at `/config` stores the stable Plex client identifier, Ed25519 private signing key, and remembered Plex account credentials for signed-in browsers. Treat this volume as sensitive and retain it across container restarts.

## Sign in and scan

1. Select **Sign in with Plex**. Plex-Clean opens Plex's approval page in a new window. If your browser blocks the window, allow pop-ups for Plex-Clean and try again.
2. Sign in to Plex and approve Plex-Clean. Keep the Plex-Clean tab open; it detects approval automatically and discovers reachable Plex Media Servers. If Plex reports that it could not complete the approval, select **Try Plex Link instead** in Plex-Clean. That opens the fallback PIN prefilled in Plex Link; approve it there without copying or entering a code. The authorization is still remembered for this browser.
3. If more than one server is available, choose the server that contains the libraries to review.
4. Select a Movie or TV library and start a scan. TV is scanned at episode level.
5. Use **Same item** to compare the media versions Plex associates with one item, or **Separate items** to review distinct entries with a matching supported ID. Expand **Show file paths and stream details** to see Plex-reported paths and individual video, audio, and subtitle streams.

In **Same item**, Plex-Clean shows one library item with multiple `Media` versions. In **Separate items**, it surfaces distinct entries with the same exact supported item ID. A shared ID is a review clue; it does not prove the files are identical or resolve every edition or cut difference. Plex-Clean does not hash files or match separate entries by title or filename. Runtime, edition, quality, size, container, codecs, streams, and paths are displayed when Plex provides them; missing values remain unavailable.

## Review and deletion

To review one version, expand its details, choose **Delete this version**, check the version information and every associated part path, then confirm. You can also select multiple versions or use **Auto-select versions for review** to prepare a batch. Auto-selection only marks versions when Plex's quality metadata supports a clear comparison; it skips edition or runtime differences, unclear quality ties, incomplete file details, and ambiguous separate-item matches. Selection alone never deletes media.

Before opening batch confirmation, Plex-Clean reloads the complete current result set, checks that each selected version still exists, and requires at least one version to remain in each affected Plex item or matching-ID group. The confirmation lists every selected title, its media details, and all associated paths; matching IDs are shown when relevant. After confirmation, Plex-Clean sends deletion requests through Plex sequentially and rechecks the selected server, library, item, media version, and part paths. It stops at the first stale result or error and reports how many requests Plex accepted before stopping, so a batch can partially complete. It does not automatically retry a destructive request.

Plex may require a server administrator account. Plex-Clean refreshes the affected results after accepted requests; use **Rescan libraries** if a follow-up read is unavailable. The app has no media-volume mount, does not delete files directly, and cannot independently verify storage after Plex responds.

## Network configuration

Plex-Clean tries reachable server connections advertised by Plex, preferring local/direct connections. If Plex returns no server resources, or the container cannot reach an advertised address, set the URL reachable from Docker in `.env` and restart:

```dotenv
PLEX_SERVER_URL=http://192.168.1.20:32400
```

Use the address and port reachable from the container, not necessarily the URL used by a browser on the host. If Plex returns no server resources, the app probes this server's identity and authenticated root before accepting it. The override does not publish or mount media directories.

## Session and access

- Plex-Clean remembers authorization separately for each browser. The Plex account JWT is stored in `/config/plex-sessions` in a mode-0600 file named from a hash of that browser's session ID; the raw Plex token is never sent to browser storage or written to logs. Plex-Clean refreshes the seven-day token through Plex using the stable Ed25519 device key.
- The `/config` volume also stores the stable client identifier and private signing key in mode-0600 files. Keep this volume private and retain it across container restarts. Removing the volume removes the remembered authorizations.
- The browser's `HttpOnly`, `SameSite=Lax` cookie lasts one year and is refreshed while the app is used. Signing out removes only that browser's saved Plex authorization. Plex revocation, cookie removal, or a definitive Plex credential rejection requires a new sign-in.
- Server connection tokens, server selection, scan state, and review results stay in app memory. A container restart or 24-hour idle-session eviction clears them; the same browser restores its Plex authorization and rediscovers servers automatically.
- For HTTPS behind a trusted reverse proxy, set `SESSION_COOKIE_SECURE=true` in `.env`.
- Keep the default HTTP endpoint on a trusted home network. Do not expose it directly to the public internet; use HTTPS and restrict access at the reverse proxy/firewall.
- The container runs as a non-root user with dropped Linux capabilities. It has no media-volume mount.

## Useful commands

```powershell
docker compose logs -f plex-clean
docker compose down
```

`docker compose down` leaves the named config volume in place. To remove it, use `docker compose down --volumes`; this removes the client identifier, signing key, and all remembered browser authorizations. It is not needed for normal sign-out or restart.
