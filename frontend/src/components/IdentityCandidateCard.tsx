import type { DuplicateGroup, ExternalMediaId, IdentityCandidate, MediaVersion } from "../types";
import VersionCard from "./VersionCard";

type Props = {
  candidate: IdentityCandidate;
  deletionDisabled: boolean;
  deletionBlockedReason: string | null;
  onDelete: (group: DuplicateGroup, version: MediaVersion, reviewedIdentity: ExternalMediaId) => void;
  isSelected: (group: DuplicateGroup, version: MediaVersion) => boolean;
  onToggleSelection: (group: DuplicateGroup, version: MediaVersion, reviewedIdentity: ExternalMediaId, selected: boolean) => void;
};

const providerName = { imdb: "IMDb", tmdb: "TMDb", tvdb: "TVDb" } as const;

function editionSummary(candidate: IdentityCandidate) {
  const labelsByNormalizedValue = new Map<string, string>();
  for (const entry of candidate.entries) {
    for (const version of entry.media_versions) {
      const label = version.edition_title?.trim();
      if (label && !labelsByNormalizedValue.has(label.toLowerCase())) {
        labelsByNormalizedValue.set(label.toLowerCase(), label);
      }
    }
  }
  const labels = [...labelsByNormalizedValue.values()];
  if (candidate.edition_review === "conflict") {
    return `Different editions are listed · ${labels.join(" / ")} · review before deleting`;
  }
  if (candidate.edition_review === "unknown") {
    return `Plex has incomplete edition details · ${labels.join(" / ")} · review before deleting`;
  }
  return labels.length ? `Plex shows matching edition details · ${labels.join(" / ")}` : "Plex shows no edition conflict";
}

function entryContext(group: DuplicateGroup) {
  if (group.library_type === "movie") return group.year ?? "Year unavailable";
  const episode = `Season ${group.season_index ?? "?"} · Episode ${group.episode_index ?? "?"}`;
  return group.parent_title ? `${group.parent_title} · ${episode}` : episode;
}

export default function IdentityCandidateCard({ candidate, deletionDisabled, deletionBlockedReason, onDelete, isSelected, onToggleSelection }: Props) {
  const editionClass = candidate.edition_review === "clear" ? "is-clear" : "is-review";
  const reviewedIdentity: ExternalMediaId = {
    provider: candidate.matched_provider,
    identifier: candidate.matched_identifier,
  };
  return (
    <article className="identity-candidate">
      <header className="identity-candidate-heading">
        <div>
          <p className="identity-eyebrow">Matching {providerName[candidate.matched_provider]} ID</p>
          <h2>{candidate.entries.length} separate Plex items</h2>
          <p className="identity-match-copy">These Plex items have the same {providerName[candidate.matched_provider]} ID. Review their versions and edition details; the match does not prove the media files are identical.</p>
        </div>
        <code className="identity-id">{providerName[candidate.matched_provider]} · {candidate.matched_identifier}</code>
      </header>
      <p className={`edition-review ${editionClass}`} role={candidate.edition_review === "clear" ? undefined : "status"}>
        <span aria-hidden="true" />{editionSummary(candidate)}
      </p>
      <div className="identity-entry-list">
        {candidate.entries.map((group) => (
          <article className="identity-entry" key={group.rating_key}>
            <header className="identity-entry-heading">
              {group.poster_url
                ? <img className="poster" src={group.poster_url} alt={`Poster for ${group.title}`} loading="lazy" />
                : <div className="poster poster-empty" aria-hidden="true">P</div>}
              <div className="identity-entry-title">
                <p>Plex item ID · {group.rating_key}</p>
                <h3>{group.title}</h3>
                <span>{entryContext(group)}</span>
              </div>
              <span className="version-count">{group.media_versions.length} {group.media_versions.length === 1 ? "version" : "versions"}</span>
            </header>
            <div className="identity-media-list">
              {group.media_versions.map((version, index) => (
                <VersionCard
                  deletionDisabled={deletionDisabled}
                  index={index}
                  key={version.media_id ?? `media-${index}`}
                  onDelete={() => onDelete(group, version, reviewedIdentity)}
                  onToggleSelection={(selected) => onToggleSelection(group, version, reviewedIdentity, selected)}
                  selected={isSelected(group, version)}
                  version={version}
                />
              ))}
            </div>
            {deletionBlockedReason && <p className="delete-note">{deletionBlockedReason}</p>}
          </article>
        ))}
      </div>
    </article>
  );
}
