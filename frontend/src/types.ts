export type LibrarySummary = {
  library_id: string;
  title: string;
  type: "movie" | "show";
  item_count: number;
  duplicate_count: number;
  identity_candidate_count: number;
  missing_identity_id_count: number;
  scan_state: "idle" | "pending" | "running" | "complete" | "failed";
};

export type ExternalMediaId = {
  provider: "imdb" | "tmdb" | "tvdb";
  identifier: string;
};

export type DeleteVersionConfirmation = {
  server_id: string;
  scan_id: string;
  library_id: string;
  confirmed_parts: { part_id: string; file: string }[];
  reviewed_identity: ExternalMediaId | null;
};

export type ServerOption = {
  machine_identifier: string;
  name: string;
  product_version: string | null;
  connections: { uri: string; local: boolean; relay: boolean; protocol: string | null }[];
  allow_media_deletion: boolean | null;
};

export type ScanError = { library_id: string | null; library_title: string | null; message: string };

export type ScanProgress = {
  scan_id: string;
  state: "pending" | "running" | "complete" | "failed";
  total_libraries: number;
  libraries_complete: number;
  current_library_id: string | null;
  current_library_title: string | null;
  items_scanned: number;
  duplicate_groups: number;
  identity_candidate_groups: number;
  items_without_supported_ids: number;
  errors: ScanError[];
};

export type PartInfo = { part_id: string | null; file: string | null; size_bytes: number | null; container: string | null };
export type StreamInfo = {
  stream_type: "video" | "audio" | "subtitle" | "unknown";
  codec: string | null;
  language: string | null;
  channels: number | null;
  channel_layout: string | null;
  width: number | null;
  height: number | null;
  frame_rate: string | null;
  profile: string | null;
  dynamic_range: string | null;
};

export type MediaVersion = {
  media_id: string | null;
  edition_title: string | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  bitrate_kbps: number | null;
  container: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  audio_channels: number | null;
  file_size_bytes: number | null;
  parts: PartInfo[];
  streams: StreamInfo[];
  shared_episode_count: number;
};

export type DuplicateGroup = {
  rating_key: string;
  library_id: string;
  library_title: string;
  library_type: "movie" | "show";
  title: string;
  year: number | null;
  parent_title: string | null;
  season_index: number | null;
  episode_index: number | null;
  poster_url: string | null;
  media_versions: MediaVersion[];
};

export type IdentityCandidate = {
  library_id: string;
  library_title: string;
  library_type: "movie" | "show";
  matched_provider: ExternalMediaId["provider"];
  matched_identifier: string;
  match_reason: "metadata_id_match";
  edition_review: "clear" | "conflict" | "unknown";
  entries: DuplicateGroup[];
};

export type SessionStatus = {
  authenticated: boolean;
  auth_pending: boolean;
  username: string | null;
  csrf_token: string;
  selected_server: ServerOption | null;
  libraries: LibrarySummary[];
  scan: ScanProgress | null;
};

export type AuthStart = { authorization_url: string; pin_code: string; expires_at: string };
export type PlexAuthMode = "auth" | "link";
export type AuthStatus = {
  authenticated: boolean;
  pending: boolean;
  username: string | null;
  servers_available: number;
  message: string | null;
};
export type DeleteResult = { deleted: boolean; group: DuplicateGroup | null; library: LibrarySummary | null; refresh_warning: boolean };
