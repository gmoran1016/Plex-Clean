import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { analyzeLowerQualityVersions, bulkSelectionKey, type BulkDeleteItem } from "./bulkSelection";
import DeleteConfirmation from "./components/DeleteConfirmation";
import DuplicateList from "./components/DuplicateList";
import IdentityCandidateList from "./components/IdentityCandidateList";
import LibrarySidebar from "./components/LibrarySidebar";
import ScanStatus from "./components/ScanStatus";
import { isPlexAuthReturn, nextRateLimitedPollDelay, shouldPollPlexAuth } from "./authPolling";
import { pollScanUpdate } from "./scanPolling";
import type { AuthStart, DuplicateGroup, IdentityCandidate, LibrarySummary, MediaVersion, ScanProgress, ServerOption, SessionStatus } from "./types";

const PAGE_SIZE = 25;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function withUpdatedLibrary(libraries: LibrarySummary[], changed: LibrarySummary | null) {
  if (!changed) return libraries;
  return libraries.map((library) => library.library_id === changed.library_id ? changed : library);
}

function totalDuplicateGroups(libraries: LibrarySummary[]) {
  return libraries.reduce((total, library) => total + library.duplicate_count, 0);
}

function totalIdentityCandidates(libraries: LibrarySummary[]) {
  return libraries.reduce((total, library) => total + library.identity_candidate_count, 0);
}

function currentGroupFromError(error: ApiError): DuplicateGroup | null | undefined {
  if (typeof error.detail !== "object" || error.detail === null || !("current_group" in error.detail)) return undefined;
  return (error.detail as { current_group: DuplicateGroup | null }).current_group;
}

export default function App() {
  const isAuthReturn = isPlexAuthReturn(window.location.search);
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [servers, setServers] = useState<ServerOption[]>([]);
  const [libraries, setLibraries] = useState<LibrarySummary[]>([]);
  const [selectedLibraryId, setSelectedLibraryId] = useState<string | null>(null);
  const [groups, setGroups] = useState<DuplicateGroup[]>([]);
  const [offset, setOffset] = useState(0);
  const [identityCandidates, setIdentityCandidates] = useState<IdentityCandidate[]>([]);
  const [identityOffset, setIdentityOffset] = useState(0);
  const [identityRefreshKey, setIdentityRefreshKey] = useState(0);
  const [identityCandidatesLoading, setIdentityCandidatesLoading] = useState(false);
  const [identityCandidatesError, setIdentityCandidatesError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"plex-versions" | "separate-entries">("plex-versions");
  const [activeScan, setActiveScan] = useState<ScanProgress | null>(null);
  const [authStart, setAuthStart] = useState<AuthStart | null>(null);
  const [loginInitiated, setLoginInitiated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [serversLoading, setServersLoading] = useState(false);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [scanStarting, setScanStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ group: DuplicateGroup; version: MediaVersion } | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [bulkSelection, setBulkSelection] = useState<Record<string, BulkDeleteItem>>({});
  const [bulkTarget, setBulkTarget] = useState<BulkDeleteItem[] | null>(null);
  const [bulkSelecting, setBulkSelecting] = useState(false);
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkCompleted, setBulkCompleted] = useState(false);
  const [bulkProgress, setBulkProgress] = useState(0);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const bulkSelectionRequest = useRef(0);

  const selectedServerId = session?.selected_server?.machine_identifier ?? null;
  const selectedLibrary = useMemo(
    () => libraries.find((library) => library.library_id === selectedLibraryId) ?? libraries[0] ?? null,
    [libraries, selectedLibraryId],
  );
  const authPending = Boolean(session?.auth_pending || loginInitiated);
  const authWatching = shouldPollPlexAuth(window.location.search, authPending);

  useEffect(() => {
    bulkSelectionRequest.current += 1;
    setBulkSelecting(false);
    setBulkSelection({});
    setIdentityOffset(0);
    setIdentityCandidates([]);
    setIdentityCandidatesError(null);
  }, [selectedLibrary?.library_id, selectedServerId, activeScan?.scan_id]);

  useEffect(() => {
    let alive = true;
    api.getSession()
      .then((current) => {
        if (!alive) return;
        setSession(current);
        setLibraries(current.libraries);
        setActiveScan(current.scan);
        setSelectedLibraryId(current.libraries[0]?.library_id ?? null);
      })
      .catch((reason: unknown) => { if (alive) setError(errorMessage(reason)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    const handleUnauthorized = () => {
      void api.getSession().then((updated) => {
        if (!alive) return;
        setSession(updated);
        setServers([]);
        setLibraries([]);
        setSelectedLibraryId(null);
        setGroups([]);
        setIdentityCandidates([]);
        setIdentityOffset(0);
        setActiveScan(null);
        setNotice("Plex asked you to sign in again.");
      }).catch(() => undefined);
    };
    window.addEventListener("plexclean:unauthorized", handleUnauthorized);
    return () => {
      alive = false;
      window.removeEventListener("plexclean:unauthorized", handleUnauthorized);
    };
  }, []);

  useEffect(() => {
    if (isAuthReturn || !session?.authenticated) return;
    let alive = true;
    setServersLoading(true);
    api.getServers()
      .then(async (available) => {
        if (!alive) return;
        setServers(available);
        const updated = await api.getSession();
        if (!alive) return;
        setSession(updated);
        setLibraries(updated.libraries);
        setActiveScan(updated.scan);
        setSelectedLibraryId((current) => current ?? updated.libraries[0]?.library_id ?? null);
        if (available.length === 0) setNotice("No reachable Plex Media Server was found. Check Docker network access or configure PLEX_SERVER_URL.");
        else setNotice(null);
      })
      .catch((reason: unknown) => { if (alive) setError(errorMessage(reason)); })
      .finally(() => { if (alive) setServersLoading(false); });
    return () => { alive = false; };
  }, [session?.authenticated]);

  useEffect(() => {
    if (!authWatching) return;
    let alive = true;
    let timer = 0;
    let rateLimitDelayMs = 0;
    const poll = async () => {
      try {
        const status = await api.getAuthStatus();
        if (!alive) return;
        setError(null);
        if (status.authenticated) {
          rateLimitDelayMs = 0;
          setLoginInitiated(false);
          setAuthStart(null);
          setNotice(status.message);
          const updated = await api.getSession();
          if (alive) setSession(updated);
          return;
        }
        if (!status.pending) {
          rateLimitDelayMs = 0;
          const updated = await api.getSession();
          if (!alive) return;
          setSession(updated);
          setLoginInitiated(false);
          setAuthStart(null);
          setNotice(status.message);
          return;
        }
      } catch (reason) {
        if (alive) {
          if (reason instanceof ApiError && reason.status === 429) {
            rateLimitDelayMs = nextRateLimitedPollDelay(reason.retryAfterSeconds, rateLimitDelayMs);
            setError(`Plex is temporarily rate limiting sign-in checks. Retrying in ${Math.ceil(rateLimitDelayMs / 1_000)} seconds.`);
            try {
              const updated = await api.getSession();
              if (alive && updated.authenticated) {
                setError(null);
                setSession(updated);
                setLoginInitiated(false);
                setAuthStart(null);
                return;
              }
            } catch { /* Preserve the rate-limit message and retry after the backoff. */ }
            if (alive) timer = window.setTimeout(poll, rateLimitDelayMs);
            return;
          }
          setError(errorMessage(reason));
          try {
            const updated = await api.getSession();
            if (alive && updated.authenticated) {
              setSession(updated);
              setLoginInitiated(false);
              setAuthStart(null);
              return;
            }
          } catch { /* Continue polling; the error above remains visible. */ }
        }
      }
      if (alive) timer = window.setTimeout(poll, 1800);
    };
    timer = window.setTimeout(poll, 900);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [authWatching]);

  useEffect(() => {
    if (!selectedServerId) {
      setLibraries([]);
      setSelectedLibraryId(null);
      return;
    }
    let alive = true;
    api.getLibraries()
      .then((items) => {
        if (!alive) return;
        setLibraries(items);
        setSelectedLibraryId((current) => items.some((item) => item.library_id === current) ? current : items[0]?.library_id ?? null);
      })
      .catch((reason: unknown) => { if (alive) setError(errorMessage(reason)); });
    return () => { alive = false; };
  }, [selectedServerId]);

  useEffect(() => {
    if (!selectedLibrary || !activeScan || (activeScan.state !== "complete" && activeScan.state !== "failed")) {
      setGroups([]);
      return;
    }
    let alive = true;
    setGroupsLoading(true);
    api.getDuplicates(selectedLibrary.library_id, offset, PAGE_SIZE)
      .then((items) => { if (alive) setGroups(items); })
      .catch((reason: unknown) => { if (alive) setError(errorMessage(reason)); })
      .finally(() => { if (alive) setGroupsLoading(false); });
    return () => { alive = false; };
  }, [selectedLibrary?.library_id, offset, activeScan?.scan_id, activeScan?.state]);

  useEffect(() => {
    if (
      viewMode !== "separate-entries"
      || !selectedLibrary
      || activeScan?.state !== "complete"
      || selectedLibrary.scan_state !== "complete"
    ) {
      setIdentityCandidates([]);
      setIdentityCandidatesLoading(false);
      setIdentityCandidatesError(null);
      return;
    }
    let alive = true;
    setIdentityCandidatesLoading(true);
    setIdentityCandidatesError(null);
    api.getIdentityCandidates(selectedLibrary.library_id, identityOffset, PAGE_SIZE)
      .then((items) => { if (alive) setIdentityCandidates(items); })
      .catch((reason: unknown) => { if (alive) setIdentityCandidatesError(errorMessage(reason)); })
      .finally(() => { if (alive) setIdentityCandidatesLoading(false); });
    return () => { alive = false; };
  }, [viewMode, selectedLibrary?.library_id, selectedLibrary?.scan_state, activeScan?.scan_id, activeScan?.state, identityOffset, identityRefreshKey]);

  useEffect(() => {
    if (!activeScan || (activeScan.state !== "pending" && activeScan.state !== "running")) return;
    let alive = true;
    let timer = 0;
    const poll = async () => {
      try {
        const update = await pollScanUpdate(activeScan.scan_id, api.getScan, api.getLibraries);
        if (!alive) return;
        setActiveScan(update.scan);
        const latestLibraries = update.libraries;
        if (latestLibraries) {
          setLibraries(latestLibraries);
          setSession((current) => current ? { ...current, libraries: latestLibraries, scan: update.scan } : current);
          setNotice(update.scan.errors.length ? `Scan finished with ${update.scan.errors.length} library issue${update.scan.errors.length === 1 ? "" : "s"}.` : null);
          return;
        }
      } catch (reason) {
        if (alive) setError(errorMessage(reason));
      }
      if (alive) timer = window.setTimeout(poll, 1400);
    };
    timer = window.setTimeout(poll, 800);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [activeScan?.scan_id, activeScan?.state]);

  const refreshSession = useCallback(async () => {
    const updated = await api.getSession();
    setSession(updated);
    setLibraries(updated.libraries);
    setActiveScan(updated.scan);
    return updated;
  }, []);

  async function refreshLibrarySummaries() {
    const latestLibraries = await api.getLibraries();
    setLibraries(latestLibraries);
    setSession((current) => current ? { ...current, libraries: latestLibraries } : current);
    if (viewMode === "separate-entries" && selectedLibrary) {
      const currentLibrary = latestLibraries.find((library) => library.library_id === selectedLibrary.library_id);
      if (currentLibrary) {
        const lastVisibleOffset = currentLibrary.identity_candidate_count > 0
          ? Math.floor((currentLibrary.identity_candidate_count - 1) / PAGE_SIZE) * PAGE_SIZE
          : 0;
        setIdentityOffset((current) => Math.min(current, lastVisibleOffset));
      }
    }
    setActiveScan((current) => current ? {
      ...current,
      duplicate_groups: totalDuplicateGroups(latestLibraries),
      identity_candidate_groups: totalIdentityCandidates(latestLibraries),
    } : current);
  }

  async function refreshIdentityAfterConflict() {
    setIdentityRefreshKey((current) => current + 1);
    if (viewMode !== "separate-entries") return;
    try { await refreshLibrarySummaries(); } catch { /* Keep the stale-result warning visible. */ }
  }

  async function startLogin() {
    if (!session) return;
    setError(null);
    setNotice(null);
    try {
      const started = await api.startPlexLogin(session.csrf_token);
      setAuthStart(started);
      setSession((current) => current ? { ...current, authenticated: false, auth_pending: true } : current);
      setLoginInitiated(true);
    } catch (reason) { setError(errorMessage(reason)); }
  }

  async function logout() {
    if (!session) return;
    try {
      await api.logout(session.csrf_token);
      const updated = await api.getSession();
      setSession(updated);
      setServers([]);
      setLibraries([]);
      setSelectedLibraryId(null);
      setGroups([]);
      setIdentityCandidates([]);
      setIdentityOffset(0);
      setIdentityCandidatesError(null);
      setActiveScan(null);
      setAuthStart(null);
      setBulkSelection({});
      setNotice("Signed out of Plex.");
      setError(null);
    } catch (reason) { setError(errorMessage(reason)); }
  }

  async function changeServer(machineId: string) {
    if (!session) return;
    setError(null);
    try {
      await api.selectServer(machineId, session.csrf_token);
      const updated = await refreshSession();
      setBulkSelection({});
      setSelectedLibraryId(updated.libraries[0]?.library_id ?? null);
      setActiveScan(null);
      setGroups([]);
      setOffset(0);
      setIdentityCandidates([]);
      setIdentityOffset(0);
      setIdentityCandidatesError(null);
    } catch (reason) { setError(errorMessage(reason)); }
  }

  async function scanLibraries() {
    if (!session) return;
    setError(null);
    setNotice(null);
    setScanStarting(true);
    setBulkSelection({});
    try {
      const started = await api.startScan(session.csrf_token);
      setActiveScan(started);
      setLibraries((current) => current.map((library) => ({
        ...library,
        duplicate_count: 0,
        identity_candidate_count: 0,
        missing_identity_id_count: 0,
        scan_state: "pending",
      })));
      setOffset(0);
      setIdentityCandidates([]);
      setIdentityOffset(0);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setScanStarting(false); }
  }

  function changeViewMode(mode: "plex-versions" | "separate-entries") {
    setViewMode(mode);
    setIdentityOffset(0);
    setIdentityCandidatesError(null);
    if (mode === "separate-entries") setBulkSelection({});
  }

  async function selectLowerQualityVersions() {
    if (!session || !selectedLibrary || deletionDisabled) return;
    if (activeScan?.state !== "complete" || selectedLibrary.scan_state !== "complete") {
      setError("Run a complete scan of this library before selecting lower-quality versions.");
      return;
    }

    const libraryId = selectedLibrary.library_id;
    const expectedCount = selectedLibrary.duplicate_count;
    const requestId = ++bulkSelectionRequest.current;
    setBulkSelecting(true);
    setError(null);
    setNotice(null);
    try {
      const pageSize = 100;
      const pageCount = Math.ceil(expectedCount / pageSize);
      const pages = await Promise.all(
        Array.from({ length: pageCount }, (_, page) => api.getDuplicates(libraryId, page * pageSize, pageSize)),
      );
      if (bulkSelectionRequest.current !== requestId) return;
      const allGroups = pages.flat();
      const uniqueRatingKeys = new Set(allGroups.map((group) => group.rating_key));
      if (allGroups.length !== expectedCount || uniqueRatingKeys.size !== expectedCount || allGroups.some((group) => group.library_id !== libraryId)) {
        throw new Error("The full duplicate list changed while it was loading. Run a fresh scan, then try again.");
      }

      const automaticallySelected: BulkDeleteItem[] = [];
      let skippedGroups = 0;
      let skippedVersions = 0;
      for (const group of allGroups) {
        const analysis = analyzeLowerQualityVersions(group);
        automaticallySelected.push(...analysis.selected);
        if (analysis.skippedReason) skippedGroups += 1;
        skippedVersions += analysis.skippedVersions;
      }
      if (bulkSelectionRequest.current !== requestId) return;
      setBulkSelection((current) => {
        const next = { ...current };
        automaticallySelected.forEach((item) => { next[item.key] = item; });
        return next;
      });
      setNotice(automaticallySelected.length
        ? `Selected ${automaticallySelected.length} clearly lower-quality versions across the whole library. ${skippedGroups} groups were left unchanged for ties, unknown quality, edition differences, or runtime variants; ${skippedVersions} copies lacked complete Plex file details.`
        : `No clearly lower-quality versions were selected. ${skippedGroups} groups were left unchanged for ties, unknown quality, edition differences, or runtime variants; ${skippedVersions} copies lacked complete Plex file details.`);
    } catch (reason) {
      if (bulkSelectionRequest.current === requestId) setError(errorMessage(reason));
    } finally {
      if (bulkSelectionRequest.current === requestId) setBulkSelecting(false);
    }
  }

  function toggleBulkSelection(group: DuplicateGroup, version: MediaVersion, selected: boolean) {
    const key = bulkSelectionKey(group, version);
    setBulkSelection((current) => {
      const next = { ...current };
      if (selected) next[key] = { key, group, version, reason: "Manually selected" };
      else delete next[key];
      return next;
    });
  }

  function requestBulkDelete() {
    const items = Object.values(bulkSelection);
    if (!items.length || deletionDisabled) return;
    setBulkTarget(items);
    setBulkProgress(0);
    setBulkCompleted(false);
    setBulkError(null);
  }

  async function confirmBulkDelete() {
    if (!bulkTarget?.length || !session || bulkPending || bulkCompleted) return;
    setBulkPending(true);
    setBulkProgress(0);
    setBulkError(null);
    setError(null);
    let confirmedDeletes = 0;
    let stopReason: string | null = null;

    for (const item of bulkTarget) {
      if (!item.version.media_id || !item.version.parts.length || item.version.parts.some((part) => !part.part_id || !part.file)) {
        stopReason = "A selected version no longer has complete Plex file identifiers. Run a fresh scan before continuing.";
        break;
      }
      try {
        const result = await api.deleteVersion(item.group.rating_key, item.version.media_id, session.csrf_token);
        confirmedDeletes += 1;
        setBulkProgress(confirmedDeletes);
        setGroups((current) => result.group
          ? current.map((group) => group.rating_key === result.group?.rating_key ? result.group! : group)
          : current.filter((group) => group.rating_key !== item.group.rating_key));
        setLibraries((current) => withUpdatedLibrary(current, result.library));
        setSession((current) => current && result.library
          ? { ...current, libraries: withUpdatedLibrary(current.libraries, result.library) }
          : current);
        setActiveScan((current) => current
          ? { ...current, duplicate_groups: Math.max(0, current.duplicate_groups - (result.group ? 0 : 1)) }
          : current);
        if (!result.group && result.library) {
          setOffset((current) => {
            const lastVisibleOffset = result.library!.duplicate_count > 0
              ? Math.floor((result.library!.duplicate_count - 1) / PAGE_SIZE) * PAGE_SIZE
              : 0;
            return Math.min(current, lastVisibleOffset);
          });
        }
        if (result.refresh_warning) {
          stopReason = "Plex accepted this delete request, but could not refresh the item afterward.";
          break;
        }
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 409) {
          const fresh = currentGroupFromError(reason);
          if (fresh !== undefined) {
            setGroups((current) => fresh
              ? current.map((group) => group.rating_key === fresh.rating_key ? fresh : group)
              : current.filter((group) => group.rating_key !== item.group.rating_key));
            if (!fresh) {
              try {
                const latestLibraries = await api.getLibraries();
                setLibraries(latestLibraries);
                setSession((current) => current ? { ...current, libraries: latestLibraries } : current);
              } catch { /* The stale response below still asks the user to rescan. */ }
            }
          }
        }
        stopReason = errorMessage(reason);
        break;
      }
    }

    setBulkSelection({});
    if (stopReason) {
      setBulkError(`${stopReason} The batch stopped after Plex confirmed ${confirmedDeletes} of ${bulkTarget.length} requested deletions. Run a fresh scan before selecting again.`);
      setBulkCompleted(true);
    } else {
      setNotice(`Plex accepted ${confirmedDeletes} selected media version delete requests.`);
      setBulkTarget(null);
      setBulkCompleted(false);
    }
    setBulkPending(false);
  }

  async function confirmDelete() {
    if (!deleteTarget || !session || !deleteTarget.version.media_id) return;
    setDeletePending(true);
    setDeleteError(null);
    setError(null);
    try {
      const result = await api.deleteVersion(deleteTarget.group.rating_key, deleteTarget.version.media_id, session.csrf_token);
      if (result.group && !result.refresh_warning) {
        setGroups((current) => current.map((group) => group.rating_key === result.group?.rating_key ? result.group! : group));
      } else if (!result.group) {
        setGroups((current) => current.filter((group) => group.rating_key !== deleteTarget.group.rating_key));
        if (offset > 0 && groups.length <= 1) setOffset((current) => Math.max(0, current - PAGE_SIZE));
      }
      setLibraries((current) => withUpdatedLibrary(current, result.library));
      setSession((current) => current && result.library
        ? { ...current, libraries: withUpdatedLibrary(current.libraries, result.library) }
        : current);
      setActiveScan((current) => current && result.library ? {
        ...current,
        duplicate_groups: Math.max(0, current.duplicate_groups + result.library.duplicate_count - (selectedLibrary?.duplicate_count ?? result.library.duplicate_count)),
        identity_candidate_groups: Math.max(0, current.identity_candidate_groups + result.library.identity_candidate_count - (selectedLibrary?.identity_candidate_count ?? result.library.identity_candidate_count)),
      } : current);
      if (viewMode === "separate-entries" && result.library) {
        const lastVisibleOffset = result.library.identity_candidate_count > 0
          ? Math.floor((result.library.identity_candidate_count - 1) / PAGE_SIZE) * PAGE_SIZE
          : 0;
        setIdentityOffset((current) => Math.min(current, lastVisibleOffset));
      }
      setNotice(result.refresh_warning
        ? "Plex accepted the delete request, but the item could not be refreshed. Rescan this library before taking another action."
        : "Plex accepted the selected media version delete request.");
      setIdentityRefreshKey((current) => current + 1);
      setBulkSelection({});
      setDeleteTarget(null);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setBulkSelection({});
        const fresh = currentGroupFromError(reason);
        if (fresh !== undefined) {
          setGroups((current) => fresh
            ? current.map((group) => group.rating_key === fresh.rating_key ? fresh : group)
            : current.filter((group) => group.rating_key !== deleteTarget.group.rating_key));
          if (viewMode === "separate-entries") {
            await refreshIdentityAfterConflict();
          } else if (!fresh) {
            try { await refreshLibrarySummaries(); } catch { /* The stale warning remains visible; the user can rescan. */ }
          }
          setDeleteTarget(null);
          setError(reason.message);
        } else setDeleteError(reason.message);
      } else setDeleteError(errorMessage(reason));
    } finally { setDeletePending(false); }
  }

  function requestDelete(group: DuplicateGroup, version: MediaVersion) {
    setDeleteError(null);
    setBulkCompleted(false);
    setBulkError(null);
    setDeleteTarget({ group, version });
  }

  const currentServer = session?.selected_server ?? null;
  const deletionDisabled = currentServer?.allow_media_deletion !== true;
  const deletionBlockedReason = currentServer?.allow_media_deletion === false
    ? "Plex reports that media deletion is disabled on this server."
    : currentServer?.allow_media_deletion === null
      ? "Plex did not confirm that media deletion is enabled; this app keeps deletion unavailable."
      : null;
  const selectedBulkItems = Object.values(bulkSelection);

  if (loading) {
    return <main className="loading-screen"><span className="loading-mark" /><p>Opening Plex-Clean…</p></main>;
  }

  if (isAuthReturn) {
    return (
      <main className="login-screen">
        <section className="login-panel">
          <Wordmark />
          <h1>{!session ? "Restoring your Plex sign-in…" : session.authenticated ? "Plex sign-in complete." : session.auth_pending ? "Checking your Plex sign-in." : "No pending Plex sign-in."}</h1>
          <p className="login-description">{!session
            ? "Restoring the app session returned by Plex."
            : session.auth_pending
              ? "Plex returned to the app. This tab is checking the approved PIN; keep it open for a moment."
              : session.authenticated
                ? "Opening your Plex libraries…"
                : "Return to Plex-Clean and start a new sign-in link."}</p>
          {error && <p className="alert alert-error" role="alert">{error}</p>}
          {!session?.authenticated && <a className="button button-primary" href="/">Return to Plex-Clean</a>}
        </section>
      </main>
    );
  }

  if (!session) {
    return <main className="login-screen"><div className="login-panel"><Wordmark /><h1>Could not start Plex-Clean</h1><p>{error ?? "Refresh this page to try again."}</p></div></main>;
  }

  if (!session.authenticated) {
    return (
      <main className="login-screen">
        <section className="login-panel">
          <Wordmark />
          <h1>Compare every version.<br /><span>Keep the one you choose.</span></h1>
          <p className="login-description">Review duplicate movie and episode versions in your Plex libraries, then remove a selected version through Plex.</p>
          {error && <p className="alert alert-error" role="alert">{error}</p>}
          {notice && <p className="alert alert-info" role="status">{notice}</p>}
          {authStart ? (
            <div className="login-pending">
              <p>Open Plex Link and enter this one-time code to authorize Plex-Clean.</p>
              <code className="plex-pin-code" aria-label="Plex sign-in code">{authStart.pin_code}</code>
              <a className="button button-primary" href={authStart.authorization_url} rel="noreferrer" target="_blank">Open Plex Link</a>
              <p>Leave this page open while Plex-Clean checks for approval.</p>
              <p className="expiry-note">PIN expires {new Date(authStart.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.</p>
            </div>
          ) : session.auth_pending || loginInitiated ? (
            <div className="login-pending">
              <p>A Plex sign-in PIN is already waiting.</p>
              <button className="button button-primary" disabled={loginInitiated} onClick={startLogin} type="button">Create a new sign-in link</button>
              <p>After opening Plex sign-in, keep this page open while the app checks for approval.</p>
            </div>
          ) : (
            <button className="button button-primary login-action" onClick={startLogin} type="button">Sign in with Plex</button>
          )}
          <p className="privacy-note">Your Plex token stays in this app's server memory and is never stored in the browser.</p>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <Wordmark />
        <div className="topbar-right">
          {servers.length > 1 ? (
            <label className="server-picker">
              <span className="server-label">PLEX SERVER</span>
              <select aria-label="Selected Plex server" onChange={(event) => void changeServer(event.target.value)} value={selectedServerId ?? ""}>
                {!selectedServerId && <option value="">Choose a server</option>}
                {servers.map((server) => <option key={server.machine_identifier} value={server.machine_identifier}>{server.name}</option>)}
              </select>
            </label>
          ) : currentServer ? (
            <div className="server-status"><span className="server-dot" />{currentServer.name}<span className="connected-text">Connected</span></div>
          ) : serversLoading ? <span className="server-status muted">Finding Plex server…</span> : null}
          <span className="topbar-divider" />
          <span className="account-name">{session.username || "Plex user"}</span>
          <button className="text-button" onClick={logout} type="button">Sign out</button>
        </div>
      </header>

      {error && <div className="global-message alert-error" role="alert"><span>{error}</span><button aria-label="Dismiss message" onClick={() => setError(null)} type="button">×</button></div>}
      {notice && <div className="global-message alert-info" role="status"><span>{notice}</span><button aria-label="Dismiss message" onClick={() => setNotice(null)} type="button">×</button></div>}

      <div className="workspace">
        <LibrarySidebar libraries={libraries} selectedId={selectedLibrary?.library_id ?? null} onSelect={(id) => { setSelectedLibraryId(id); setOffset(0); setIdentityOffset(0); }} />
        <main className="main-content">
          {!selectedServerId ? (
            <section className="content-empty server-empty">
              <h1>{serversLoading ? "Finding your Plex server" : servers.length ? "Choose a Plex server" : "No reachable Plex server"}</h1>
              <p>{serversLoading
                ? "Checking the server connections available to your account."
                : servers.length
                  ? "Select the Plex Media Server whose movie and TV libraries you want to review."
                  : "Make sure Docker can reach your Plex Media Server, then retry. You can also set PLEX_SERVER_URL in Compose."}</p>
              {!serversLoading && !servers.length && <button className="button button-secondary" onClick={() => void api.getServers().then(setServers).then(refreshSession).catch((reason) => setError(errorMessage(reason)))} type="button">Try again</button>}
            </section>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <h1>{viewMode === "plex-versions" ? "Duplicate versions" : "Separate Plex entries"}</h1>
                  <p>{viewMode === "plex-versions"
                    ? "Compare media versions attached to one Plex item."
                    : "Review distinct Plex items that share an exact metadata ID."}</p>
                </div>
                {selectedLibrary && <p className="active-library-note">{selectedLibrary.title}</p>}
              </div>
              <ScanStatus scan={activeScan} onScan={scanLibraries} busy={scanStarting} disabled={!currentServer} />
              <div className="review-mode-switch" role="group" aria-label="Duplicate review mode">
                <button
                  aria-pressed={viewMode === "plex-versions"}
                  className={viewMode === "plex-versions" ? "is-active" : ""}
                  onClick={() => changeViewMode("plex-versions")}
                  type="button"
                >
                  Plex versions <span>{selectedLibrary?.duplicate_count.toLocaleString() ?? "0"}</span>
                </button>
                <button
                  aria-pressed={viewMode === "separate-entries"}
                  className={viewMode === "separate-entries" ? "is-active" : ""}
                  onClick={() => changeViewMode("separate-entries")}
                  type="button"
                >
                  Separate entries <span>{selectedLibrary?.identity_candidate_count.toLocaleString() ?? "0"}</span>
                </button>
              </div>
              {activeScan?.errors.map((scanError) => (
                <p className="library-scan-error" key={`${scanError.library_id ?? "unknown"}-${scanError.message}`}>
                  <strong>{scanError.library_title ?? "Library"}:</strong> {scanError.message}
                </p>
              ))}
              {viewMode === "plex-versions" && selectedLibrary && selectedLibrary.duplicate_count > 0 && (
                <section className="bulk-toolbar" aria-label="Bulk version selection">
                  <div className="bulk-toolbar-copy">
                    <strong>{selectedBulkItems.length} {selectedBulkItems.length === 1 ? "version" : "versions"} selected</strong>
                    <span>Selection carries across pages. Automatic selection checks the whole library and skips ties, unknown quality, different editions, or runtimes differing by over one minute or 1%, whichever is greater.</span>
                    {bulkSelecting && <span role="status">Comparing every duplicate group in {selectedLibrary.title}…</span>}
                  </div>
                  <div className="bulk-toolbar-actions">
                    <button
                      className="button button-secondary"
                      disabled={deletionDisabled || bulkSelecting || activeScan?.state !== "complete" || selectedLibrary.scan_state !== "complete"}
                      onClick={() => void selectLowerQualityVersions()}
                      type="button"
                    >
                      Select lower-quality across library
                    </button>
                    <button
                      className="button button-secondary"
                      disabled={!selectedBulkItems.length || bulkSelecting || bulkPending}
                      onClick={() => setBulkSelection({})}
                      type="button"
                    >
                      Clear selection
                    </button>
                    <button
                      className="button button-danger"
                      disabled={!selectedBulkItems.length || deletionDisabled || bulkSelecting || bulkPending}
                      onClick={requestBulkDelete}
                      type="button"
                    >
                      Review &amp; delete{selectedBulkItems.length ? ` (${selectedBulkItems.length})` : ""}
                    </button>
                  </div>
                </section>
              )}
              {viewMode === "plex-versions" ? (
                <DuplicateList
                  groups={groups}
                  library={selectedLibrary}
                  scan={activeScan}
                  offset={offset}
                  limit={PAGE_SIZE}
                  loading={groupsLoading}
                  deletionDisabled={deletionDisabled || bulkSelecting || bulkPending}
                  deletionBlockedReason={deletionBlockedReason}
                  isSelected={(group, version) => Boolean(bulkSelection[bulkSelectionKey(group, version)])}
                  onPage={setOffset}
                  onDelete={requestDelete}
                  onToggleSelection={toggleBulkSelection}
                />
              ) : (
                <IdentityCandidateList
                  candidates={identityCandidates}
                  library={selectedLibrary}
                  scan={activeScan}
                  offset={identityOffset}
                  limit={PAGE_SIZE}
                  loading={identityCandidatesLoading}
                  error={identityCandidatesError}
                  deletionDisabled={deletionDisabled}
                  deletionBlockedReason={deletionBlockedReason}
                  onPage={setIdentityOffset}
                  onRetry={() => setIdentityRefreshKey((current) => current + 1)}
                  onDelete={requestDelete}
                />
              )}
              {currentServer?.allow_media_deletion === true && <p className="permission-note">Plex permits media deletion on this server; Plex may still require a server administrator account.</p>}
            </>
          )}
        </main>
      </div>

      <DeleteConfirmation
        group={deleteTarget?.group ?? null}
        version={deleteTarget?.version ?? null}
        bulkItems={bulkTarget}
        pending={deletePending || bulkPending}
        completed={Boolean(bulkTarget && bulkCompleted)}
        progress={bulkProgress}
        error={bulkTarget ? bulkError : deleteError}
        onCancel={() => {
          if (deletePending || bulkPending) return;
          setDeleteTarget(null);
          setDeleteError(null);
          setBulkTarget(null);
          setBulkError(null);
          setBulkCompleted(false);
        }}
        onConfirm={() => { if (bulkTarget) void confirmBulkDelete(); else void confirmDelete(); }}
      />
    </div>
  );
}

function Wordmark() {
  return <a className="wordmark" href="/" aria-label="Plex-Clean home"><strong>Plex</strong><span /> <span>Duplicate Review</span></a>;
}
