import type { DuplicateGroup, MediaVersion } from "../types";
import VersionCard, { formatDuration } from "./VersionCard";

type Props = {
  group: DuplicateGroup;
  deletionDisabled: boolean;
  deletionBlockedReason: string | null;
  isSelected: (version: MediaVersion) => boolean;
  onDelete: (group: DuplicateGroup, version: MediaVersion) => void;
  onToggleSelection: (group: DuplicateGroup, version: MediaVersion, selected: boolean) => void;
};

function runtimeDifference(versions: MediaVersion[]) {
  const [first, second] = versions;
  if (versions.length !== 2 || first.duration_ms === null || second.duration_ms === null) return null;
  const seconds = Math.round((second.duration_ms - first.duration_ms) / 1000);
  if (seconds === 0) return "Same runtime";
  const magnitude = Math.abs(seconds);
  const formatted = magnitude >= 3600
    ? `${Math.floor(magnitude / 3600)}:${String(Math.floor((magnitude % 3600) / 60)).padStart(2, "0")}`
    : `${Math.floor(magnitude / 60)}:${String(magnitude % 60).padStart(2, "0")}`;
  return `${seconds > 0 ? "+" : "−"}${formatted}`;
}

export default function DuplicateGroupCard({ group, deletionDisabled, deletionBlockedReason, isSelected, onDelete, onToggleSelection }: Props) {
  const hasTwo = group.media_versions.length === 2;
  return (
    <article className="duplicate-dossier">
      <header className="dossier-heading">
        {group.poster_url ? <img className="poster" src={group.poster_url} alt={`Poster for ${group.title}`} loading="lazy" /> : <div className="poster poster-empty" aria-hidden="true">P</div>}
        <div className="dossier-title-block">
          {group.library_type === "show" && group.parent_title && <p className="show-context">{group.parent_title}</p>}
          <h2>{group.title}</h2>
          <p className="dossier-context">
            {group.library_type === "movie"
              ? group.year ?? "Year unavailable"
              : `Season ${group.season_index ?? "?"} · Episode ${group.episode_index ?? "?"}${group.year ? ` · ${group.year}` : ""}`}
          </p>
        </div>
        <span className="version-count">{group.media_versions.length} versions</span>
      </header>
      <div className={`version-comparison${hasTwo ? " has-runtime-delta" : ""}`}>
        {group.media_versions.map((version, index) => (
          <div className="version-cell" key={version.media_id ?? `version-${index}`}>
            <VersionCard
              version={version}
              index={index}
              deletionDisabled={deletionDisabled}
              selected={isSelected(version)}
              onToggleSelection={(selected) => onToggleSelection(group, version, selected)}
              onDelete={() => onDelete(group, version)}
            />
            {hasTwo && index === 0 && (
              <div className="runtime-delta" aria-label={`Runtime difference ${runtimeDifference(group.media_versions) ?? "Unavailable"}`}>
                <strong>{runtimeDifference(group.media_versions) ?? formatDuration(null)}</strong>
                <span>runtime difference</span>
              </div>
            )}
          </div>
        ))}
      </div>
      {deletionBlockedReason && <p className="delete-note">{deletionBlockedReason}</p>}
    </article>
  );
}
