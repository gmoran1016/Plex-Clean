import type { DuplicateGroup, ExternalMediaId, IdentityCandidate, MediaVersion } from "./types";
import type { BulkDeleteItem } from "./bulkSelection";
import { bulkSelectionKey } from "./bulkSelection.ts";

type Dimensions = { pixels: number; width: number; height: number };

function dimensions(version: MediaVersion): Dimensions | null {
  if (!version.width || !version.height || version.width <= 0 || version.height <= 0) return null;
  return { pixels: version.width * version.height, width: version.width, height: version.height };
}

function hasIdentifiableParts(version: MediaVersion) {
  return Boolean(version.media_id)
    && !(version.shared_episode_count > 0)
    && version.parts.length > 0
    && version.parts.every((part) => Boolean(part.part_id && part.file));
}

function reviewedIdentity(candidate: IdentityCandidate): ExternalMediaId {
  return { provider: candidate.matched_provider, identifier: candidate.matched_identifier };
}

function compareCandidate(candidate: IdentityCandidate): { selected: BulkDeleteItem[]; skippedVersions: number } | null {
  if (candidate.edition_review !== "clear" || candidate.entries.length < 2) return null;

  const versions: { group: DuplicateGroup; version: MediaVersion; dimensions: Dimensions }[] = [];
  for (const group of candidate.entries) {
    if (group.media_versions.length !== 1) return null;
    const version = group.media_versions[0];
    const quality = dimensions(version);
    if (!quality) return null;
    versions.push({ group, version, dimensions: quality });
  }

  const durations = versions.map(({ version }) => version.duration_ms);
  if (durations.some((duration) => duration === null || duration <= 0)) return null;
  const longestRuntime = Math.max(...durations as number[]);
  const shortestRuntime = Math.min(...durations as number[]);
  if (longestRuntime - shortestRuntime > Math.max(60_000, longestRuntime * 0.01)) return null;

  const dimensionPairs = new Map<number, Set<string>>();
  versions.forEach(({ dimensions: value }) => {
    const pairs = dimensionPairs.get(value.pixels) ?? new Set<string>();
    pairs.add(`${value.width}x${value.height}`);
    dimensionPairs.set(value.pixels, pairs);
  });
  if ([...dimensionPairs.values()].some((pairs) => pairs.size > 1)) return null;

  const bestResolution = Math.max(...versions.map(({ dimensions: value }) => value.pixels));
  const topIndexes = versions.flatMap(({ dimensions: value }, index) => value.pixels === bestResolution ? [index] : []);
  let bestIndex = topIndexes[0];
  let bestBitrate: number | null = null;
  if (topIndexes.length > 1) {
    const topBitrates = topIndexes.map((index) => versions[index].version.bitrate_kbps);
    if (topBitrates.some((bitrate) => bitrate === null || bitrate <= 0)) return null;
    bestBitrate = Math.max(...topBitrates as number[]);
    const bestIndexes = topIndexes.filter((index) => versions[index].version.bitrate_kbps === bestBitrate);
    if (bestIndexes.length !== 1) return null;
    bestIndex = bestIndexes[0];
  }

  const best = versions[bestIndex];
  const selected: BulkDeleteItem[] = [];
  let skippedVersions = 0;
  versions.forEach((entry, index) => {
    if (index === bestIndex) return;
    let reason: string;
    if (entry.dimensions.pixels < best.dimensions.pixels) {
      reason = `Lower resolution (${entry.dimensions.width} × ${entry.dimensions.height} vs ${best.dimensions.width} × ${best.dimensions.height})`;
    } else {
      const bitrate = entry.version.bitrate_kbps;
      if (bestBitrate === null || bitrate === null || bitrate <= 0 || bitrate >= bestBitrate) {
        skippedVersions += 1;
        return;
      }
      reason = `Lower bitrate (${bitrate.toLocaleString()} vs ${bestBitrate.toLocaleString()} kb/s at the same resolution)`;
    }
    if (!hasIdentifiableParts(entry.version)) {
      skippedVersions += 1;
      return;
    }
    selected.push({
      key: bulkSelectionKey(entry.group, entry.version),
      group: entry.group,
      version: entry.version,
      reason: `${reason}; retaining ${best.group.title}`,
      reviewedIdentity: reviewedIdentity(candidate),
    });
  });

  return { selected, skippedVersions };
}

export function analyzeSafeIdentityDuplicates(candidates: IdentityCandidate[]) {
  const candidateIndexesByRatingKey = new Map<string, Set<number>>();
  candidates.forEach((candidate, index) => {
    candidate.entries.forEach((entry) => {
      const indexes = candidateIndexesByRatingKey.get(entry.rating_key) ?? new Set<number>();
      indexes.add(index);
      candidateIndexesByRatingKey.set(entry.rating_key, indexes);
    });
  });
  const overlappingIndexes = new Set<number>();
  for (const indexes of candidateIndexesByRatingKey.values()) {
    if (indexes.size > 1) indexes.forEach((index) => overlappingIndexes.add(index));
  }

  const selected: BulkDeleteItem[] = [];
  let skippedCandidates = 0;
  let skippedVersions = 0;
  candidates.forEach((candidate, index) => {
    if (overlappingIndexes.has(index)) {
      skippedCandidates += 1;
      return;
    }
    const analysis = compareCandidate(candidate);
    if (!analysis) {
      skippedCandidates += 1;
      return;
    }
    selected.push(...analysis.selected);
    skippedVersions += analysis.skippedVersions;
  });
  return { selected, skippedCandidates, skippedVersions };
}

export function findCandidatesWithoutSurvivors(candidates: IdentityCandidate[], selectedKeys: Set<string>) {
  return candidates.filter((candidate) => !candidate.entries.some((group) =>
    group.media_versions.some((version) => !selectedKeys.has(bulkSelectionKey(group, version))),
  ));
}

export function findDuplicateGroupsWithoutSurvivors(groups: DuplicateGroup[], selectedKeys: Set<string>) {
  return groups.filter((group) => !group.media_versions.some((version) => !selectedKeys.has(bulkSelectionKey(group, version))));
}
