import type { LibrarySummary, ScanProgress } from "./types";

export type ScanPollUpdate = {
  scan: ScanProgress;
  libraries?: LibrarySummary[];
};

export async function pollScanUpdate(
  scanId: string,
  getScan: (id: string) => Promise<ScanProgress>,
  getLibraries: () => Promise<LibrarySummary[]>,
): Promise<ScanPollUpdate> {
  const scan = await getScan(scanId);
  if (scan.state !== "complete" && scan.state !== "failed") return { scan };

  return { scan, libraries: await getLibraries() };
}
