import type {
  AuthStart,
  AuthStatus,
  DeleteVersionConfirmation,
  DeleteResult,
  DuplicateGroup,
  IdentityCandidate,
  LibrarySummary,
  ScanProgress,
  ServerOption,
  SessionStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function readRetryAfterSeconds(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  if (value === null) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? Math.ceil(seconds) : null;
}

async function request<T>(path: string, init: RequestInit = {}, csrfToken?: string): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError("Could not connect to Plex-Clean. Check that the app is running.", 0, null);
  }
  if (response.status === 204) return undefined as T;

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("plexclean:unauthorized"));
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? (payload as { detail: unknown }).detail
      : payload;
    const message = typeof detail === "string"
      ? detail
      : typeof detail === "object" && detail !== null && "message" in detail
        ? String((detail as { message: unknown }).message)
        : "Plex-Clean could not complete this request. Refresh the page and check the current results before trying again.";
    throw new ApiError(message, response.status, detail, readRetryAfterSeconds(response));
  }
  return payload as T;
}

export const api = {
  getSession: () => request<SessionStatus>("/api/session"),
  startPlexLogin: (csrf: string) => request<AuthStart>("/api/auth/pin", { method: "POST" }, csrf),
  getAuthStatus: () => request<AuthStatus>("/api/auth/status"),
  logout: (csrf: string) => request<void>("/api/auth/logout", { method: "POST" }, csrf),
  getServers: () => request<ServerOption[]>("/api/servers"),
  selectServer: (machineIdentifier: string, csrf: string) =>
    request<ServerOption>("/api/servers/selection", {
      method: "PUT",
      body: JSON.stringify({ machine_identifier: machineIdentifier }),
    }, csrf),
  getLibraries: () => request<LibrarySummary[]>("/api/libraries"),
  startScan: (csrf: string) => request<ScanProgress>("/api/scan", { method: "POST" }, csrf),
  getScan: (scanId: string) => request<ScanProgress>(`/api/scan/${encodeURIComponent(scanId)}`),
  getDuplicates: (libraryId: string, offset: number, limit = 25) => {
    const query = new URLSearchParams({ library_id: libraryId, offset: String(offset), limit: String(limit) });
    return request<DuplicateGroup[]>(`/api/duplicates?${query.toString()}`);
  },
  getIdentityCandidates: (libraryId: string, offset: number, limit = 25) => {
    const query = new URLSearchParams({ library_id: libraryId, offset: String(offset), limit: String(limit) });
    return request<IdentityCandidate[]>(`/api/identity-candidates?${query.toString()}`);
  },
  deleteVersion: (ratingKey: string, mediaId: string, confirmation: DeleteVersionConfirmation, csrf: string) =>
    request<DeleteResult>(
      `/api/duplicates/${encodeURIComponent(ratingKey)}/media/${encodeURIComponent(mediaId)}/delete`,
      { method: "POST", body: JSON.stringify(confirmation) },
      csrf,
    ),
};
