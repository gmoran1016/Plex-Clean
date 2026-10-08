const MIN_RATE_LIMIT_DELAY_MS = 60_000;
const MAX_RATE_LIMIT_DELAY_MS = 5 * 60_000;

export function isPlexAuthReturn(search: string): boolean {
  return new URLSearchParams(search).get("plexAuthReturn") === "1";
}

export function shouldPollPlexAuth(search: string, authPending: boolean): boolean {
  return !isPlexAuthReturn(search) && authPending;
}

export function nextRateLimitedPollDelay(
  retryAfterSeconds: number | null,
  previousDelayMs: number,
): number {
  const advisedDelayMs = retryAfterSeconds !== null && Number.isFinite(retryAfterSeconds)
    ? Math.max(0, Math.ceil(retryAfterSeconds * 1_000))
    : 0;
  const exponentialDelayMs = previousDelayMs > 0
    ? previousDelayMs * 2
    : MIN_RATE_LIMIT_DELAY_MS;
  return Math.min(
    MAX_RATE_LIMIT_DELAY_MS,
    Math.max(MIN_RATE_LIMIT_DELAY_MS, exponentialDelayMs, advisedDelayMs),
  );
}
