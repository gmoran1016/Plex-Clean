# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is a self-hosted Plex server administrator running Plex-Clean in Docker on a private network. They review movie and TV library entries to understand where multiple media copies exist and decide what, if anything, to remove.

## Product Purpose

Plex-Clean scans Plex Movie and TV libraries, presents Plex-reported versions and metadata for comparison, and helps the administrator review duplicate candidates before requesting cleanup through Plex. Success means the administrator can make an informed, explicit decision while Plex-Clean acts only on the exact media versions they reviewed.

## Positioning

Plex-Clean uses Plex's own multiple-version records to identify versions attached to one library item. It also surfaces separate entries that share an exact supported IMDb, TMDb, or TVDb item ID as candidates for review. It shows Plex-provided details without claiming that files are byte-identical or that shared IDs resolve every edition or cut ambiguity.

## Operating Context

- The app is self-hosted as a Docker Compose service and uses Plex sign-in; there is no separate Plex-Clean account.
- The container needs network access to Plex.tv for sign-in and server discovery, and to the selected Plex Media Server for library and deletion operations.
- The administrator selects a reachable server when more than one is available, then scans Movie and TV libraries. TV results are reviewed at episode level.
- Results are session-scoped. Restarting the container clears the session and scan results, so the administrator signs in again.
- The default endpoint is intended for a trusted home network. Remote access requires HTTPS and access restrictions at the reverse proxy or firewall.

## Capabilities and Constraints

- Plex PIN sign-in, reachable server selection, library scans with progress, duplicate-version comparison, and separate-entry candidate review using exact supported item IDs.
- Comparison details include Plex-provided title and episode context, edition, runtime, resolution, bitrate, size, container, codecs, streams, and paths when available. Missing values remain unknown.
- A user can request deletion of one reviewed version through Plex. Separate-entry candidates also support individual selections and a constrained safe-selection batch; the complete batch is reviewed and confirmed before sequential deletion. Selection alone never deletes media.
- Before deletion, Plex-Clean rechecks the selected server, library, item, media identifiers, and associated parts and paths. It stops on stale data or an error and does not automatically retry a destructive request.
- Plex-Clean does not mount or access media directories, hash files, or match distinct entries by title or filename. It cannot independently verify storage after Plex responds.
- Plex account and server tokens remain in server memory and expire from use after 24 hours. The `/config` volume retains the client identifier and device signing key; neither secret is sent to browser storage or logs.
- Music, photos, and other non-video library types are outside scope. Deletion requires Plex server permission.
- Open decision: the user requested an individual-entry ignore option, but the ignored unit, persistence, restore behavior, and interaction with shared-ID candidates have not been approved.

## Evidence on Hand

The repository contains the working React/Vite frontend, FastAPI backend, Docker Compose setup, operating README, and an approved product specification. Plex-Clean displays data returned by Plex, including supported item IDs and media details when available. The repository documents no customer testimonials, usage metrics, or third-party product claims; future materials must not invent them.

## Product Principles

- Treat Plex data as the source for versions, identifiers, and media details; keep missing data and unresolved identity differences visible.
- Use exact supported item IDs to surface separate-entry candidates, without presenting a match as proof that files are identical.
- Keep deletion deliberate: show the reviewed selection and paths, validate them against current Plex data, and require final confirmation.
- Use Plex for media deletion and avoid direct access to the media filesystem.
- Keep Plex credentials server-side and make session-scoped results clear to the administrator.
