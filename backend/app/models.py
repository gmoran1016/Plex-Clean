"""Public API models. Secret-bearing Plex credentials are intentionally absent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConnectionSummary(ApiModel):
    uri: str
    local: bool = False
    relay: bool = False
    protocol: str | None = None


class ServerOption(ApiModel):
    machine_identifier: str
    name: str
    product_version: str | None = None
    connections: list[ServerConnectionSummary] = Field(default_factory=list)
    allow_media_deletion: bool | None = None


class LibrarySummary(ApiModel):
    library_id: str
    title: str
    type: Literal["movie", "show"]
    item_count: int = 0
    duplicate_count: int = 0
    identity_candidate_count: int = 0
    missing_identity_id_count: int = 0
    scan_state: Literal["idle", "pending", "running", "complete", "failed"] = "idle"


class ScanError(ApiModel):
    library_id: str | None = None
    library_title: str | None = None
    message: str


class ScanProgress(ApiModel):
    scan_id: str
    state: Literal["pending", "running", "complete", "failed"]
    total_libraries: int = 0
    libraries_complete: int = 0
    current_library_id: str | None = None
    current_library_title: str | None = None
    items_scanned: int = 0
    duplicate_groups: int = 0
    identity_candidate_groups: int = 0
    items_without_supported_ids: int = 0
    errors: list[ScanError] = Field(default_factory=list)


class PartInfo(ApiModel):
    part_id: str | None = None
    file: str | None = None
    size_bytes: int | None = None
    container: str | None = None


class StreamInfo(ApiModel):
    stream_type: Literal["video", "audio", "subtitle", "unknown"]
    codec: str | None = None
    language: str | None = None
    channels: int | None = None
    channel_layout: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: str | None = None
    profile: str | None = None
    dynamic_range: str | None = None


class MediaVersion(ApiModel):
    media_id: str | None = None
    edition_title: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    bitrate_kbps: int | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    file_size_bytes: int | None = None
    parts: list[PartInfo] = Field(default_factory=list)
    streams: list[StreamInfo] = Field(default_factory=list)


class DuplicateGroup(ApiModel):
    rating_key: str
    library_id: str
    library_title: str
    library_type: Literal["movie", "show"]
    title: str
    year: int | None = None
    parent_title: str | None = None
    season_index: int | None = None
    episode_index: int | None = None
    poster_url: str | None = None
    media_versions: list[MediaVersion]


class ExternalMediaId(ApiModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["imdb", "tmdb", "tvdb"]
    identifier: str = Field(min_length=1)


class IdentityCandidate(ApiModel):
    library_id: str
    library_title: str
    library_type: Literal["movie", "show"]
    matched_provider: Literal["imdb", "tmdb", "tvdb"]
    matched_identifier: str
    match_reason: Literal["metadata_id_match"] = "metadata_id_match"
    edition_review: Literal["clear", "conflict", "unknown"]
    entries: list[DuplicateGroup]


class AuthStart(ApiModel):
    authorization_url: str
    pin_code: str
    expires_at: datetime


class AuthStatus(ApiModel):
    authenticated: bool = False
    pending: bool = False
    username: str | None = None
    servers_available: int = 0
    message: str | None = None


class SessionStatus(ApiModel):
    authenticated: bool = False
    auth_pending: bool = False
    username: str | None = None
    csrf_token: str
    selected_server: ServerOption | None = None
    libraries: list[LibrarySummary] = Field(default_factory=list)
    scan: ScanProgress | None = None


class DeleteResult(ApiModel):
    deleted: bool
    group: DuplicateGroup | None = None
    library: LibrarySummary | None = None
    refresh_warning: bool = False
