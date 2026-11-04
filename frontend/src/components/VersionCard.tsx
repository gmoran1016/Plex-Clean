import type { MediaVersion } from "../types";
import StreamDetails from "./StreamDetails";

export function formatDuration(durationMs: number | null) {
  if (durationMs === null || !Number.isFinite(durationMs)) return "Unavailable";
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}` : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatBytes(bytes: number | null) {
  if (bytes === null || !Number.isFinite(bytes)) return "Unavailable";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatResolution(width: number | null, height: number | null) {
  if (!width || !height) return "Unavailable";
  const tier = height >= 2000 ? "4K" : height >= 1000 ? "1080p" : height >= 700 ? "720p" : `${height}p`;
  return `${tier} · ${width} × ${height}`;
}

type Props = {
  version: MediaVersion;
  index: number;
  deletionDisabled: boolean;
  selected: boolean;
  onToggleSelection: (selected: boolean) => void;
  onDelete: () => void;
  deleteLabel?: string;
  showBulkSelection?: boolean;
};

function display(value: string | number | null | undefined) {
  return value === null || value === undefined || value === "" ? "Unavailable" : String(value);
}

export default function VersionCard({ version, index, deletionDisabled, selected, onToggleSelection, onDelete, deleteLabel = "Delete this version", showBulkSelection = true }: Props) {
  const partsAreIdentifiable = version.parts.length > 0 && version.parts.every((part) => Boolean(part.part_id && part.file));
  const sharedEpisodeFile = version.shared_episode_count > 0;
  const deleteDisabled = deletionDisabled || !version.media_id || !partsAreIdentifiable || sharedEpisodeFile;
  return (
    <section className="version-column" aria-label={`Version ${index + 1}`}>
      <div className="version-heading">
        {showBulkSelection ? (
          <label className="version-selection">
            <input
              aria-label={`Add version ${index + 1} to the delete review`}
              checked={selected}
              disabled={deleteDisabled}
              onChange={(event) => onToggleSelection(event.target.checked)}
              type="checkbox"
            />
            <span>Version {index + 1}</span>
          </label>
        ) : <span className="version-label">Version {index + 1}</span>}
        {version.edition_title && <span className="edition-label">{version.edition_title}</span>}
      </div>
      <dl className="version-facts">
        <div><dt>Quality</dt><dd>{formatResolution(version.width, version.height)}</dd></div>
        <div><dt>Runtime</dt><dd>{formatDuration(version.duration_ms)}</dd></div>
        <div><dt>File size</dt><dd>{formatBytes(version.file_size_bytes)}</dd></div>
        <div><dt>Container</dt><dd>{display(version.container)}</dd></div>
        <div><dt>Video</dt><dd>{display(version.video_codec)}</dd></div>
        <div><dt>Audio</dt><dd>{version.audio_channels ? `${display(version.audio_codec)} · ${version.audio_channels} ch` : display(version.audio_codec)}</dd></div>
        <div><dt>Bitrate</dt><dd>{version.bitrate_kbps ? `${version.bitrate_kbps.toLocaleString()} kb/s` : "Unavailable"}</dd></div>
      </dl>
      <details className="version-details">
        <summary>Show file paths and stream details</summary>
        <div className="detail-content">
          <div className="part-list">
            <h4>Files Plex associates with this version</h4>
            {version.parts.length ? version.parts.map((part, partIndex) => (
              <div className="part-row" key={`${part.part_id ?? "part"}-${partIndex}`}>
                <code>{display(part.file)}</code>
                <span>{formatBytes(part.size_bytes)}{part.container ? ` · ${part.container}` : ""}</span>
              </div>
            )) : <p className="detail-empty">File paths unavailable.</p>}
          </div>
          <div className="stream-section">
            <h4>Streams</h4>
            <StreamDetails streams={version.streams} />
          </div>
        </div>
      </details>
      <button className="button button-danger-outline" disabled={deleteDisabled} onClick={onDelete} type="button">
        {deleteLabel}
      </button>
      {(!version.media_id || !partsAreIdentifiable) && <p className="delete-note">Plex did not provide all the media details needed to delete this version. Try a new scan.</p>}
      {sharedEpisodeFile && (
        <p className="delete-note" role="status">
          Plex links this file to {version.shared_episode_count} other {version.shared_episode_count === 1 ? "episode" : "episodes"}. Deletion is disabled because Plex may remove all episodes in the shared file.
        </p>
      )}
    </section>
  );
}
