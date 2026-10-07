import { useEffect, useRef } from "react";
import type { BulkDeleteItem } from "../bulkSelection";
import type { DuplicateGroup, MediaVersion } from "../types";
import { formatBytes, formatDuration, formatResolution } from "./VersionCard";

type Props = {
  group: DuplicateGroup | null;
  version: MediaVersion | null;
  bulkItems: BulkDeleteItem[] | null;
  pending: boolean;
  completed: boolean;
  progress: number;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
};

function display(value: string | number | null | undefined) {
  return value === null || value === undefined || value === "" ? "Unavailable" : String(value);
}

export default function DeleteConfirmation({
  group,
  version,
  bulkItems,
  pending,
  completed,
  progress,
  error,
  onCancel,
  onConfirm,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const bulkMode = Boolean(bulkItems?.length);
  const singleMode = Boolean(group && version);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if ((singleMode || bulkMode) && !dialog.open) dialog.showModal();
    if (!singleMode && !bulkMode && dialog.open) dialog.close();
  }, [bulkMode, singleMode]);

  if (!singleMode && !bulkMode) return <dialog ref={dialogRef} className="delete-dialog" />;

  const singleIndex = singleMode && group && version ? group.media_versions.indexOf(version) : -1;
  const bulkCount = bulkItems?.length ?? 0;
  const affectedTitles = new Set(bulkItems?.map((item) => item.group.rating_key) ?? []);

  return (
    <dialog
      aria-labelledby="delete-dialog-title"
      className="delete-dialog"
      onCancel={(event) => { event.preventDefault(); if (!pending) onCancel(); }}
      ref={dialogRef}
    >
      <div className="dialog-content">
        <button aria-label="Close confirmation" className="dialog-close" disabled={pending} onClick={onCancel} type="button">×</button>
        {bulkMode ? (
          <>
            <h2 id="delete-dialog-title">Delete {bulkCount} selected versions?</h2>
            <p className="dialog-lede">
              Plex will delete every listed source file across {affectedTitles.size} {affectedTitles.size === 1 ? "item" : "items"}. Review the paths before continuing.
            </p>
            {pending && (
              <div className="bulk-progress" role="status">
                <progress max={bulkCount} value={progress} />
                <span>Deleting {progress} of {bulkCount} versions…</span>
              </div>
            )}
            <ol className="bulk-confirmation-list" aria-label="Selected versions and affected files">
              {bulkItems!.map((item) => (
                <li key={item.key}>
                  <h3>{item.group.title}{item.group.year ? ` (${item.group.year})` : ""}</h3>
                  <p>
                    {item.group.library_title}
                    {item.group.parent_title ? ` · ${item.group.parent_title}` : ""}
                    {item.group.season_index !== null ? ` · S${item.group.season_index ?? "?"} E${item.group.episode_index ?? "?"}` : ""}
                  </p>
                  <dl>
                    <div><dt>Quality</dt><dd>{formatResolution(item.version.width, item.version.height)}</dd></div>
                    <div><dt>Runtime</dt><dd>{formatDuration(item.version.duration_ms)}</dd></div>
                    <div><dt>Size</dt><dd>{formatBytes(item.version.file_size_bytes)}</dd></div>
                    <div><dt>Container</dt><dd>{display(item.version.container)}</dd></div>
                    <div><dt>Video</dt><dd>{display(item.version.video_codec)}</dd></div>
                    <div><dt>Audio</dt><dd>{item.version.audio_channels ? `${display(item.version.audio_codec)} · ${item.version.audio_channels} ch` : display(item.version.audio_codec)}</dd></div>
                    <div><dt>Bitrate</dt><dd>{item.version.bitrate_kbps ? `${item.version.bitrate_kbps.toLocaleString()} kb/s` : "Unavailable"}</dd></div>
                    <div><dt>Edition</dt><dd>{item.version.edition_title || "Unavailable"}</dd></div>
                    <div><dt>Selected because</dt><dd>{item.reason}</dd></div>
                  </dl>
                  <div className="bulk-file-list">
                    <strong>Files Plex will remove</strong>
                    <ul>
                      {item.version.parts.map((part, index) => (
                        <li key={`${part.part_id ?? "part"}-${index}`}><code>{part.file || "Path unavailable"}</code></li>
                      ))}
                    </ul>
                  </div>
                </li>
              ))}
            </ol>
          </>
        ) : (
          <>
            <h2 id="delete-dialog-title">Delete this version?</h2>
            <p className="dialog-lede">Plex will delete the source media files for this version.</p>
            <section className="selected-version" aria-label="Selected version details">
              {group!.poster_url ? <img src={group!.poster_url} alt="" /> : <div className="dialog-poster-empty" aria-hidden="true">P</div>}
              <div>
                <h3>{group!.title}{group!.year ? ` (${group!.year})` : ""}</h3>
                <p>{group!.library_title}{group!.parent_title ? ` · ${group!.parent_title}` : ""}</p>
                <dl>
                  <div><dt>Selected</dt><dd>Version {singleIndex + 1}</dd></div>
                  <div><dt>Quality</dt><dd>{formatResolution(version!.width, version!.height)}</dd></div>
                  <div><dt>Runtime</dt><dd>{formatDuration(version!.duration_ms)}</dd></div>
                  <div><dt>Size</dt><dd>{formatBytes(version!.file_size_bytes)}</dd></div>
                  <div><dt>Container</dt><dd>{display(version!.container)}</dd></div>
                  <div><dt>Video encoding</dt><dd>{display(version!.video_codec)}</dd></div>
                  <div><dt>Audio encoding</dt><dd>{version!.audio_channels ? `${display(version!.audio_codec)} · ${version!.audio_channels} ch` : display(version!.audio_codec)}</dd></div>
                  <div><dt>Bitrate</dt><dd>{version!.bitrate_kbps ? `${version!.bitrate_kbps.toLocaleString()} kb/s` : "Unavailable"}</dd></div>
                  <div><dt>Edition</dt><dd>{version!.edition_title || "Unavailable"}</dd></div>
                </dl>
              </div>
            </section>
            <section className="affected-files">
              <h3>Files included in this version</h3>
              {version!.parts.length ? (
                <ul>
                  {version!.parts.map((part, partIndex) => <li key={`${part.part_id ?? "part"}-${partIndex}`}><code>{part.file || "Path unavailable"}</code></li>)}
                </ul>
              ) : <p>File paths unavailable in Plex.</p>}
              {version!.parts.length > 1 && <p className="multi-part-note">This version contains {version!.parts.length} parts. Plex will remove all listed parts.</p>}
            </section>
          </>
        )}

        {error && <p className="dialog-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button className="button button-secondary" disabled={pending} onClick={onCancel} type="button">
            {completed ? "Close review" : bulkMode ? "Cancel batch" : "Keep version"}
          </button>
          <button className="button button-danger" disabled={pending || completed} onClick={onConfirm} type="button">
            {pending
              ? bulkMode ? `Deleting ${progress} / ${bulkCount}…` : "Deleting…"
              : completed ? "Batch stopped" : bulkMode ? `Delete ${bulkCount} versions` : "Delete version"}
          </button>
        </div>
      </div>
    </dialog>
  );
}
