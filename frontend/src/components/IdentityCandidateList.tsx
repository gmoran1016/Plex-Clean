import type { DuplicateGroup, IdentityCandidate, LibrarySummary, MediaVersion, ScanProgress } from "../types";
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
  onPage: (offset: number) => void;
  onRetry: () => void;
  onDelete: (group: DuplicateGroup, version: MediaVersion) => void;
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
  onPage,
  onRetry,
  onDelete,
}: Props) {
  if (!library) {
    return <section className="content-empty"><h2>No movie or TV libraries</h2><p>Only Plex Movie and TV sections appear here.</p></section>;
  }
  if (!scan || scan.state === "pending" || scan.state === "running") {
    return <section className="content-empty"><h2>Run a library scan</h2><p>Separate Plex entries will appear here after Plex has been scanned.</p></section>;
  }
  if (library.scan_state !== "complete") {
    return <section className="content-empty"><h2>This library could not be scanned</h2><p>Check server access and try scanning again.</p></section>;
  }
  if (loading) return <div className="content-empty" role="status"><span className="loading-mark" /> Loading separate-entry candidates…</div>;
  if (error) {
    return (
      <section className="content-empty">
        <h2>Could not load separate-entry candidates</h2>
        <p role="alert">{error}</p>
        <button className="button button-secondary" onClick={onRetry} type="button">Try again</button>
      </section>
    );
  }

  const coverage = library.missing_identity_id_count
    ? `${library.missing_identity_id_count.toLocaleString()} items in this library have no supported IMDb, TMDb, or TVDb item ID and could not be compared.`
    : "Every scanned item had at least one supported IMDb, TMDb, or TVDb item ID.";
  if (!library.identity_candidate_count || !candidates.length) {
    return (
      <section className="identity-empty-state">
        <div className="identity-coverage" role="note">{coverage}</div>
        <div className="content-empty">
          <h2>No separate Plex entries found</h2>
          <p>{library.title} has no scanned entries sharing an exact supported item ID.</p>
        </div>
      </section>
    );
  }

  const end = Math.min(offset + candidates.length, library.identity_candidate_count);
  return (
    <section className="identity-results" aria-label={`Separate Plex entry candidates in ${library.title}`}>
      <div className="identity-coverage" role="note">{coverage}</div>
      <div className="identity-candidate-list">
        {candidates.map((candidate) => (
          <IdentityCandidateCard
            candidate={candidate}
            deletionBlockedReason={deletionBlockedReason}
            deletionDisabled={deletionDisabled}
            key={`${candidate.matched_provider}:${candidate.matched_identifier}`}
            onDelete={onDelete}
          />
        ))}
      </div>
      <nav className="pagination" aria-label="Separate Plex entry pages">
        <span>{offset + 1}–{end} of {library.identity_candidate_count.toLocaleString()}</span>
        <div>
          <button className="button button-secondary" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))} type="button">Previous</button>
          <button className="button button-secondary" disabled={end >= library.identity_candidate_count} onClick={() => onPage(offset + limit)} type="button">Next</button>
        </div>
      </nav>
    </section>
  );
}
