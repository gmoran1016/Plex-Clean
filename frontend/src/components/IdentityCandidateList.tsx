import type { DuplicateGroup, ExternalMediaId, IdentityCandidate, LibrarySummary, MediaVersion, ScanProgress } from "../types";
import IdentityCandidateCard from "./IdentityCandidateCard";

type Props = {
  candidates: IdentityCandidate[];
  library: LibrarySummary | null;
  scan: ScanProgress | null;
  offset: number;
  limit: number;
  loading: boolean;
  error: string | null;
  deletionDisabled: boolean;
  deletionBlockedReason: string | null;
  isSelected: (group: DuplicateGroup, version: MediaVersion) => boolean;
  onPage: (offset: number) => void;
  onRetry: () => void;
  onDelete: (group: DuplicateGroup, version: MediaVersion, reviewedIdentity: ExternalMediaId) => void;
  onToggleSelection: (group: DuplicateGroup, version: MediaVersion, reviewedIdentity: ExternalMediaId, selected: boolean) => void;
};

export default function IdentityCandidateList({
  candidates,
  library,
  scan,
  offset,
  limit,
  loading,
  error,
  deletionDisabled,
  deletionBlockedReason,
  isSelected,
  onPage,
  onRetry,
  onDelete,
  onToggleSelection,
}: Props) {
  if (!library) {
    return <section className="content-empty"><h2>No Movie or TV libraries</h2><p>Plex-Clean reviews Movie and TV libraries only.</p></section>;
  }
  if (!scan || scan.state === "pending" || scan.state === "running") {
    return <section className="content-empty"><h2>Scan this library</h2><p>Separate Plex items with the same IMDb, TMDb, or TVDb ID will appear here. A shared ID is a review clue, not proof the files are identical.</p></section>;
  }
  if (library.scan_state !== "complete") {
    return <section className="content-empty"><h2>Plex could not scan this library</h2><p>Check that Plex-Clean can access the library, then scan again.</p></section>;
  }
  if (loading) return <div className="content-empty" role="status"><span className="loading-mark" /> Loading matching Plex items…</div>;
  if (error) {
    return (
      <section className="content-empty">
        <h2>Could not load matching Plex items</h2>
        <p role="alert">{error}</p>
        <button className="button button-secondary" onClick={onRetry} type="button">Try again</button>
      </section>
    );
  }

  const coverage = library.missing_identity_id_count
    ? `${library.missing_identity_id_count.toLocaleString()} items have no IMDb, TMDb, or TVDb ID, so Plex-Clean could not compare them here.`
    : "Every scanned item has an IMDb, TMDb, or TVDb ID this comparison can use.";
  if (!library.identity_candidate_count || !candidates.length) {
    return (
      <section className="identity-empty-state">
        <div className="identity-coverage" role="note">{coverage}</div>
        <div className="content-empty">
          <h2>No matching Plex items found</h2>
          <p>No separate items in {library.title} share the same IMDb, TMDb, or TVDb ID.</p>
        </div>
      </section>
    );
  }

  const end = Math.min(offset + candidates.length, library.identity_candidate_count);
  return (
    <section className="identity-results" aria-label={`Separate Plex items with matching IDs in ${library.title}`}>
      <div className="identity-coverage" role="note">{coverage}</div>
      <div className="identity-candidate-list">
        {candidates.map((candidate) => (
          <IdentityCandidateCard
            candidate={candidate}
            deletionBlockedReason={deletionBlockedReason}
            deletionDisabled={deletionDisabled}
            isSelected={isSelected}
            key={`${candidate.matched_provider}:${candidate.matched_identifier}`}
            onDelete={onDelete}
            onToggleSelection={onToggleSelection}
          />
        ))}
      </div>
      <nav className="pagination" aria-label="Matching Plex item pages">
        <span>{offset + 1}–{end} of {library.identity_candidate_count.toLocaleString()}</span>
        <div>
          <button className="button button-secondary" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))} type="button">Previous</button>
          <button className="button button-secondary" disabled={end >= library.identity_candidate_count} onClick={() => onPage(offset + limit)} type="button">Next</button>
        </div>
      </nav>
    </section>
  );
}
