import type { ScanProgress } from "../types";

type Props = { scan: ScanProgress | null; onScan: () => void; busy: boolean; disabled: boolean };

export default function ScanStatus({ scan, onScan, busy, disabled }: Props) {
  const running = busy || scan?.state === "pending" || scan?.state === "running";
  const complete = scan?.state === "complete" && scan.errors.length === 0;
  return (
    <section className="scan-status" aria-label="Library scan">
      <div className="scan-copy" aria-live="polite">
        <span className={`scan-indicator${running ? " is-running" : complete ? " is-complete" : ""}`} aria-hidden="true" />
        <div>
          <strong>{running ? "Scanning libraries" : complete ? "Scan complete" : scan?.errors.length ? "Scan finished with issues" : "Ready to scan"}</strong>
          <span className="scan-caption">
            {running
              ? `${scan?.current_library_title ?? "Getting ready"} · ${scan?.items_scanned.toLocaleString() ?? "0"} items scanned · ${scan?.items_without_supported_ids.toLocaleString() ?? "0"} without an IMDb, TMDb, or TVDb ID`
              : scan?.state === "complete" || scan?.state === "failed"
                ? `${scan.duplicate_groups.toLocaleString()} items with multiple versions · ${scan.identity_candidate_groups.toLocaleString()} matching-ID groups · ${scan.items_without_supported_ids.toLocaleString()} items without an IMDb, TMDb, or TVDb ID`
                : "Scan your movie and TV libraries to compare media versions"}
          </span>
        </div>
      </div>
      <button className="button button-secondary scan-button" disabled={disabled || running} onClick={onScan} type="button">
        <span aria-hidden="true" className={running ? "spin-mark" : "refresh-mark"}>{running ? "···" : "↻"}</span>
        {running ? "Scanning" : scan ? "Rescan libraries" : "Scan libraries"}
      </button>
    </section>
  );
}
