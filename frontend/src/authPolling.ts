const MIN_RATE_LIMIT_DELAY_MS = 60_000;
const MAX_RATE_LIMIT_DELAY_MS = 5 * 60_000;

export function openPlexAuthPopup<T>(
  openWindow: (url: string, target: string, features: string) => T | null,
): T | null {
  return openWindow("about:blank", "plex-clean-plex-auth", "popup=yes,width=600,height=700");
}

export function shouldPollPlexAuth(authPending: boolean): boolean {
  return authPending;
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
