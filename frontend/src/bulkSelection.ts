import type { DuplicateGroup, ExternalMediaId, MediaVersion } from "./types";

export type BulkDeleteItem = {
  key: string;
  group: DuplicateGroup;
  version: MediaVersion;
  reason: string;
  reviewedIdentity: ExternalMediaId | null;
};

export type LowerQualityAnalysis = {
  selected: BulkDeleteItem[];
  skippedReason: string | null;
  skippedVersions: number;
};

export function bulkSelectionKey(group: DuplicateGroup, version: MediaVersion) {
  return `${group.rating_key}:${version.media_id ?? "missing"}`;
}

function normalizedEdition(version: MediaVersion) {
  return version.edition_title?.trim().toLocaleLowerCase() ?? "";
}

function hasIdentifiableParts(version: MediaVersion) {
  return Boolean(version.media_id)
    && !(version.shared_episode_count > 0)
    && version.parts.length > 0
    && version.parts.every((part) => Boolean(part.part_id && part.file));
}

function dimensions(version: MediaVersion) {
  if (!version.width || !version.height || version.width <= 0 || version.height <= 0) return null;
  return { pixels: version.width * version.height, width: version.width, height: version.height };
}

function normalizedText(value: string | null) {
  return value?.trim().toLowerCase() || null;
}

function technicalDetailsSignature(version: MediaVersion) {
  if (!version.duration_ms || version.duration_ms <= 0
    || !version.bitrate_kbps || version.bitrate_kbps <= 0
    || !version.file_size_bytes || version.file_size_bytes <= 0
    || !version.container?.trim()
    || !version.video_codec?.trim()
    || !version.audio_codec?.trim()
    || !version.audio_channels || version.audio_channels <= 0
    || !version.parts.length
    || version.parts.some((part) => !part.size_bytes || part.size_bytes <= 0)
    || version.streams.some((stream) => stream.stream_type === "unknown" || !stream.codec?.trim())) {
    return null;
  }

  const videoStreams = version.streams.filter((stream) => stream.stream_type === "video");
  if (version.streams.length > 0
    && (!videoStreams.length || videoStreams.some((stream) => !stream.width || !stream.height || stream.width <= 0 || stream.height <= 0))) {
    return null;
  }

  const parts = version.parts.map((part) => ({
    size_bytes: part.size_bytes,
    container: normalizedText(part.container),
  })).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  const streams = version.streams.map((stream) => ({
    stream_type: stream.stream_type,
    codec: normalizedText(stream.codec),
    language: normalizedText(stream.language),
    channels: stream.channels,
    channel_layout: normalizedText(stream.channel_layout),
    width: stream.width,
    height: stream.height,
    frame_rate: normalizedText(stream.frame_rate),
    profile: normalizedText(stream.profile),
    dynamic_range: normalizedText(stream.dynamic_range),
  })).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));

  return JSON.stringify({
    duration_ms: version.duration_ms,
    width: version.width,
    height: version.height,
    bitrate_kbps: version.bitrate_kbps,
    container: normalizedText(version.container),
    video_codec: normalizedText(version.video_codec),
    audio_codec: normalizedText(version.audio_codec),
    audio_channels: version.audio_channels,
    file_size_bytes: version.file_size_bytes,
    parts,
    streams,
  });
}

function haveMatchingTechnicalDetails(versions: MediaVersion[]) {
  const signatures = versions.map(technicalDetailsSignature);
  return signatures.every((signature) => signature !== null && signature === signatures[0]);
}

export function analyzeLowerQualityVersions(group: DuplicateGroup): LowerQualityAnalysis {
  const versions = group.media_versions;
  if (versions.length < 2) return { selected: [], skippedReason: "Only one version remains", skippedVersions: 0 };

  if (new Set(versions.map(normalizedEdition)).size > 1) {
    return { selected: [], skippedReason: "Editions differ", skippedVersions: 0 };
  }

  const durations = versions.map((version) => version.duration_ms);
  if (durations.some((duration) => duration === null || duration <= 0)) {
    return { selected: [], skippedReason: "Runtime is unavailable", skippedVersions: 0 };
  }
  const longestRuntime = Math.max(...durations as number[]);
  const shortestRuntime = Math.min(...durations as number[]);
  if (longestRuntime - shortestRuntime > Math.max(60_000, longestRuntime * 0.01)) {
    return { selected: [], skippedReason: "Runtime differs by more than 1 minute or 1%", skippedVersions: 0 };
  }

  const quality = versions.map(dimensions);
  if (quality.some((value) => value === null)) {
    return { selected: [], skippedReason: "Resolution is unavailable", skippedVersions: 0 };
  }

  const dimensionPairs = new Map<number, Set<string>>();
  quality.forEach((value) => {
    if (!value) return;
    const pairs = dimensionPairs.get(value.pixels) ?? new Set<string>();
    pairs.add(`${value.width}x${value.height}`);
    dimensionPairs.set(value.pixels, pairs);
  });
  if ([...dimensionPairs.values()].some((pairs) => pairs.size > 1)) {
    return { selected: [], skippedReason: "Aspect ratios make resolution ranking unclear", skippedVersions: 0 };
  }

  const bestResolution = Math.max(...quality.map((value) => value!.pixels));
  const topResolutionIndexes = quality.flatMap((value, index) => value!.pixels === bestResolution ? [index] : []);
  let bestIndex = topResolutionIndexes[0];
  let bestBitrate: number | null = null;
  let tiedBestIndexes: number[] = [];
  let tiedBestCopiesMatch = false;
  if (topResolutionIndexes.length > 1) {
    const topBitrates = topResolutionIndexes.map((index) => versions[index].bitrate_kbps);
    if (topBitrates.some((bitrate) => bitrate === null || bitrate <= 0)) {
      return { selected: [], skippedReason: "Top-resolution bitrate is unavailable", skippedVersions: 0 };
    }
    bestBitrate = Math.max(...topBitrates as number[]);
    tiedBestIndexes = topResolutionIndexes.filter((index) => versions[index].bitrate_kbps === bestBitrate);
    bestIndex = tiedBestIndexes[0];
    if (tiedBestIndexes.length > 1) {
      tiedBestCopiesMatch = haveMatchingTechnicalDetails(tiedBestIndexes.map((index) => versions[index]));
    }
  }

  const bestDimensions = quality[bestIndex]!;
  const selected: BulkDeleteItem[] = [];
  let skippedVersions = 0;
  versions.forEach((version, index) => {
    if (index === bestIndex) return;
    const current = quality[index]!;
    let reason: string;
    if (current.pixels < bestDimensions.pixels) {
      reason = `Lower resolution (${current.width} × ${current.height} vs ${bestDimensions.width} × ${bestDimensions.height})`;
    } else if (tiedBestIndexes.includes(index)) {
      if (!tiedBestCopiesMatch) return;
      reason = version.streams.length
        ? "Technical details match the retained version (quality tied)"
        : "Core file details match; Plex stream details are unavailable (quality tied)";
    } else {
      const bitrate = version.bitrate_kbps;
      if (bestBitrate === null || bitrate === null || bitrate <= 0 || bitrate >= bestBitrate) {
        skippedVersions += 1;
        return;
      }
      reason = `Lower bitrate (${bitrate.toLocaleString()} vs ${bestBitrate.toLocaleString()} kb/s at the same resolution)`;
    }
    if (!hasIdentifiableParts(version)) {
      skippedVersions += 1;
      return;
    }
    selected.push({ key: bulkSelectionKey(group, version), group, version, reason, reviewedIdentity: null });
  });

  const skippedReason = tiedBestIndexes.length > 1 && !tiedBestCopiesMatch
    ? "Top-quality versions are tied and technical details differ"
    : null;
  return { selected, skippedReason, skippedVersions };
}
