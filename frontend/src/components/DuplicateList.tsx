import type { DuplicateGroup, LibrarySummary, ScanProgress, MediaVersion } from "../types";
import DuplicateGroupCard from "./DuplicateGroupCard";

type Props = {
  groups: DuplicateGroup[];
  library: LibrarySummary | null;
  scan: ScanProgress | null;
  offset: number;
  limit: number;
  loading: boolean;
  deletionDisabled: boolean;
  deletionBlockedReason: string | null;
  isSelected: (group: DuplicateGroup, version: MediaVersion) => boolean;
  onPage: (offset: number) => void;
  onDelete: (group: DuplicateGroup, version: MediaVersion) => void;
  onToggleSelection: (group: DuplicateGroup, version: MediaVersion, selected: boolean) => void;
};

export default function DuplicateList({ groups, library, scan, offset, limit, loading, deletionDisabled, deletionBlockedReason, isSelected, onPage, onDelete, onToggleSelection }: Props) {
  if (!library) return <section className="content-empty"><h2>No Movie or TV libraries</h2><p>Plex-Clean reviews Movie and TV libraries only.</p></section>;
  if (loading) return <div className="content-empty" role="status"><span className="loading-mark" /> Loading versions…</div>;
  if (scan?.state !== "complete" && scan?.state !== "failed") {
    return <section className="content-empty"><h2>Scan this library</h2><p>Plex items with multiple media versions will appear here after the scan.</p></section>;
  }
  if (library.scan_state === "failed" && groups.length === 0) {
    return <section className="content-empty"><h2>Plex could not scan this library</h2><p>Check that Plex-Clean can access the library, then scan again.</p></section>;
  }
  if (!library.duplicate_count) {
    return <section className="content-empty"><h2>No items with multiple versions</h2><p>{library.title} has no Plex items with more than one media version.</p></section>;
  }
  const end = Math.min(offset + groups.length, library.duplicate_count);
  return (
    <section className="duplicate-list" aria-label={`Plex items with multiple versions in ${library.title}`}>
      {groups.map((group) => (
        <DuplicateGroupCard
          group={group}
          deletionDisabled={deletionDisabled}
          deletionBlockedReason={deletionBlockedReason}
          isSelected={(version) => isSelected(group, version)}
          key={group.rating_key}
          onDelete={onDelete}
          onToggleSelection={(targetGroup, version, selected) => onToggleSelection(targetGroup, version, selected)}
        />
      ))}
      <nav className="pagination" aria-label="Version results pages">
        <span>{offset + 1}–{end} of {library.duplicate_count.toLocaleString()}</span>
        <div>
          <button className="button button-secondary" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))} type="button">Previous</button>
          <button className="button button-secondary" disabled={end >= library.duplicate_count} onClick={() => onPage(offset + limit)} type="button">Next</button>
        </div>
      </nav>
    </section>
  );
}
