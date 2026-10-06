"""Exact external-provider identity matching for separately indexed Plex items."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence
from urllib.parse import urlsplit

from app.models import DuplicateGroup, ExternalMediaId, IdentityCandidate, LibrarySummary

_SUPPORTED_PROVIDERS = {"imdb", "tmdb", "tvdb"}
_PROVIDER_PRIORITY = {"imdb": 0, "tmdb": 1, "tvdb": 2}


def _external_identifier(value: Any) -> ExternalMediaId | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or any(character.isspace() for character in raw) or "://" not in raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    provider = parsed.scheme.lower()
    if provider not in _SUPPORTED_PROVIDERS or parsed.username is not None or parsed.password is not None:
        return None

    # Plex provider IDs are usually the URI authority (imdb://tt123) and can
    # also include a path (tmdb://movie/123). Query/fragment values are
    # agent metadata, so only the authority and path identify the media.
    authority = parsed.netloc
    if not authority and not parsed.path:
        return None
    if "@" in authority:
        return None
    identifier = f"{authority}{parsed.path}" if authority else parsed.path
    identifier = identifier.strip("/")
    if not identifier:
        return None
    return ExternalMediaId(provider=provider, identifier=identifier)


def external_identity_ids(item: dict[str, Any]) -> tuple[ExternalMediaId, ...]:
    """Read supported IDs from only an item's Plex ``Guid[].id`` values."""
    raw_guid_values = item.get("Guid")
    if not isinstance(raw_guid_values, list):
        return ()
    result: list[ExternalMediaId] = []
    seen: set[ExternalMediaId] = set()
    for value in raw_guid_values:
        if not isinstance(value, dict):
            continue
        external_id = _external_identifier(value.get("id"))
        if external_id is None or external_id in seen:
            continue
        seen.add(external_id)
        result.append(external_id)
    return tuple(result)


def _edition_review(entries: Sequence[DuplicateGroup]) -> str:
    per_entry: list[set[str]] = []
    display_labels: set[str] = set()
    for entry in entries:
        labels = {
            version.edition_title.strip().casefold()
            for version in entry.media_versions
            if version.edition_title and version.edition_title.strip()
        }
        per_entry.append(labels)
        display_labels.update(labels)

    if len(display_labels) > 1 or any(len(labels) > 1 for labels in per_entry):
        return "conflict"
    if any(per_entry) and not all(per_entry):
        return "unknown"
    return "clear"


def build_identity_candidates(
    entries: Sequence[tuple[DuplicateGroup, tuple[ExternalMediaId, ...]]],
    library: LibrarySummary,
) -> list[IdentityCandidate]:
    """Group exact IDs within one library without joining overlapping buckets."""
    buckets: dict[ExternalMediaId, dict[str, DuplicateGroup]] = defaultdict(dict)
    for group, identities in entries:
        if group.library_id != library.library_id or group.library_type != library.type:
            continue
        for identity in identities:
            if identity.provider not in _SUPPORTED_PROVIDERS or not identity.identifier:
                continue
            buckets[identity].setdefault(group.rating_key, group)

    # Keep one card where different providers identify precisely the same
    # member set. Partial overlaps stay as distinct candidates.
    selected: dict[frozenset[str], tuple[ExternalMediaId, tuple[DuplicateGroup, ...]]] = {}
    for identity, members_by_key in buckets.items():
        if len(members_by_key) < 2:
            continue
        member_keys = frozenset(members_by_key)
        members = tuple(sorted(members_by_key.values(), key=lambda group: group.rating_key))
        current = selected.get(member_keys)
        identity_order = (_PROVIDER_PRIORITY[identity.provider], identity.provider, identity.identifier)
        if current is None or identity_order < (
            _PROVIDER_PRIORITY[current[0].provider], current[0].provider, current[0].identifier
        ):
            selected[member_keys] = (identity, members)

    candidates = [
        IdentityCandidate(
            library_id=library.library_id,
            library_title=library.title,
            library_type=library.type,
            matched_provider=identity.provider,
            matched_identifier=identity.identifier,
            edition_review=_edition_review(members),
            entries=list(members),
        )
        for identity, members in selected.values()
    ]
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.entries[0].rating_key,
            _PROVIDER_PRIORITY[candidate.matched_provider],
            candidate.matched_identifier,
        ),
    )
