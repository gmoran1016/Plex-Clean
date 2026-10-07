import type { LibrarySummary } from "../types";

type Props = {
  libraries: LibrarySummary[];
  selectedId: string | null;
  onSelect: (libraryId: string) => void;
};

export default function LibrarySidebar({ libraries, selectedId, onSelect }: Props) {
  if (!libraries.length) {
    return <aside className="library-rail"><p className="rail-empty">No movie or TV libraries were found.</p></aside>;
  }

  return (
    <aside className="library-rail" aria-label="Plex libraries">
      <p className="rail-heading">LIBRARIES</p>
      <nav className="library-nav">
        {libraries.map((library) => (
          <button
            aria-current={selectedId === library.library_id ? "page" : undefined}
            className={`library-link${selectedId === library.library_id ? " is-active" : ""}`}
            key={library.library_id}
            onClick={() => onSelect(library.library_id)}
            type="button"
          >
            <span className="library-kind">{library.type === "movie" ? "MOVIE" : "TV"}</span>
            <span className="library-copy">
              <span className="library-title">{library.title}</span>
                <span className="library-count">{library.duplicate_count.toLocaleString()} version groups · {library.identity_candidate_count.toLocaleString()} shared-ID candidates</span>
            </span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
