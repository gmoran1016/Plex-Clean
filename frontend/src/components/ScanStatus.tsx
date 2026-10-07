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
              ? `${scan?.current_library_title ?? "Preparing"} · ${scan?.items_scanned.toLocaleString() ?? "0"} items reviewed · ${scan?.items_without_supported_ids.toLocaleString() ?? "0"} without supported IDs`
              : scan?.state === "complete" || scan?.state === "failed"
                ? `${scan.duplicate_groups.toLocaleString()} Plex-version groups · ${scan.identity_candidate_groups.toLocaleString()} shared-ID candidates · ${scan.items_without_supported_ids.toLocaleString()} items without supported IDs`
                : "Review versions in your movie and TV libraries"}
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
