"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import type {
  ConnectedRepository,
  GithubRepoSummary,
} from "@codepilot/shared-types";
import { apiFetch, ApiError } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

function logout(): Promise<void> {
  return apiFetch<void>("/api/auth/logout", { method: "POST" });
}

// How often to re-fetch the connected list while a repo is queued/indexing.
const POLL_INTERVAL_MS = 2500;

function Badge({ children }: { children: string }) {
  return (
    <span className="rounded-full bg-black/[.05] px-2 py-0.5 text-xs text-zinc-600 dark:bg-white/[.08] dark:text-zinc-400">
      {children}
    </span>
  );
}

function RepoMeta({
  fullName,
  description,
  language,
  isPrivate,
}: {
  fullName: string;
  description: string | null;
  language: string | null;
  isPrivate: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-zinc-950 dark:text-zinc-50">
          {fullName}
        </span>
        <Badge>{isPrivate ? "Private" : "Public"}</Badge>
        {language ? <Badge>{language}</Badge> : null}
      </div>
      {description ? (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {description}
        </p>
      ) : null}
    </div>
  );
}

// Statuses that mean "the worker is on it" — while a repo is in one of
// these, its Index button is disabled and the page polls for updates.
const PENDING_INDEXING_STATUSES = new Set(["queued", "indexing"]);

function isPendingIndexingStatus(status: string): boolean {
  return PENDING_INDEXING_STATUSES.has(status);
}

// Dot color per status, same convention as `HealthBadge`'s
// `STATE_DOT_CLASS`: amber while waiting, pulsing amber while active,
// emerald on success, red on failure. `not_indexed` has no entry — it
// renders as plain text with no dot.
const STATUS_DOT_CLASS: Record<string, string> = {
  queued: "bg-amber-400",
  indexing: "bg-amber-400 animate-pulse",
  indexed: "bg-emerald-500",
  failed: "bg-red-500",
};

const MAX_INDEXING_ERROR_LENGTH = 140;

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

// Small relative-time formatter built on the native `Intl` API — no new
// date library needed for a "3 hours ago"-style label. Locale is pinned
// (not the runtime default) so this page — SSR'd on first load like any
// client component — renders identically on the server and the browser;
// the two `Date.now()` calls can still differ by the render/hydration gap,
// which is rendered with `suppressHydrationWarning` at the call site.
function formatRelativeTime(iso: string): string {
  const diffSeconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  const divisions: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["week", 60 * 60 * 24 * 7],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, secondsInUnit] of divisions) {
    if (Math.abs(diffSeconds) >= secondsInUnit) {
      return rtf.format(Math.round(diffSeconds / secondsInUnit), unit);
    }
  }
  return rtf.format(diffSeconds, "second");
}

// Absolute-time label for the `title` tooltip. Locale and time zone are
// both pinned (rather than left to the runtime default) for the same
// reason as `formatRelativeTime` — `toLocaleString()` with no arguments
// resolves its default locale differently on the Node server than in the
// browser, which produces a real hydration mismatch (confirmed while
// eyeballing this page: the server rendered "09/09/2026, 12:24:20" and
// the client rendered "9/9/2026, 12:24:20 PM" for the same instant).
function formatAbsoluteTime(iso: string): string {
  const formatted = new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  });
  return `${formatted} UTC`;
}

function indexButtonLabel(status: string, isRequesting: boolean): string {
  if (isRequesting) {
    return "Starting…";
  }
  switch (status) {
    case "queued":
      return "Queued…";
    case "indexing":
      return "Indexing…";
    case "indexed":
    case "failed":
      return "Re-index";
    default:
      return "Index";
  }
}

// Status indicator shown next to each connected repo, driven entirely by
// `indexing_status`. Follows `HealthBadge`'s dot + label style.
function IndexingStatus({ repo }: { repo: ConnectedRepository }) {
  const dotClass = STATUS_DOT_CLASS[repo.indexing_status];
  const dot = dotClass ? (
    <span
      aria-hidden
      className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`}
    />
  ) : null;

  if (repo.indexing_status === "indexed") {
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400"
      >
        {dot}
        <span>
          Indexed · {repo.file_count} files · {repo.chunk_count} chunks
          {repo.indexed_at ? (
            <>
              {" · "}
              <span
                title={formatAbsoluteTime(repo.indexed_at)}
                suppressHydrationWarning
              >
                {formatRelativeTime(repo.indexed_at)}
              </span>
            </>
          ) : null}
        </span>
      </div>
    );
  }

  if (repo.indexing_status === "failed") {
    return (
      <div
        role="status"
        className="flex max-w-xs items-start gap-2 text-sm text-red-600 dark:text-red-400"
      >
        {dot}
        <span className="break-words" title={repo.indexing_error ?? undefined}>
          {repo.indexing_error
            ? truncate(repo.indexing_error, MAX_INDEXING_ERROR_LENGTH)
            : "Indexing failed"}
        </span>
      </div>
    );
  }

  if (repo.indexing_status === "queued" || repo.indexing_status === "indexing") {
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400"
      >
        {dot}
        <span>{repo.indexing_status === "queued" ? "Queued…" : "Indexing…"}</span>
      </div>
    );
  }

  return (
    <div
      role="status"
      className="text-sm text-zinc-500 dark:text-zinc-400"
    >
      Not indexed
    </div>
  );
}

export default function RepositoriesPage() {
  const router = useRouter();
  const { user, loading: userLoading } = useCurrentUser();

  // --- Repositories already connected in our DB ---
  const [connectedRepos, setConnectedRepos] = useState<
    ConnectedRepository[] | null
  >(null);
  const [connectedLoading, setConnectedLoading] = useState(true);
  const [connectedError, setConnectedError] = useState<string | null>(null);

  // Guards against out-of-order responses — the initial load, the polling
  // interval, a manual "Try again", and the post-index resync can all call
  // this while an earlier request is still in flight, and a slower earlier
  // response resolving after a newer one must not clobber fresher data.
  // Same pattern as `requestId` in `hooks/use-current-user.ts`.
  const connectedRequestId = useRef(0);

  // Fires the request and settles state via `.then/.catch/.finally` only —
  // no setState call runs synchronously in this function's own body, so
  // it's safe to invoke directly from an effect (calling something that
  // sets state synchronously inside an effect causes an extra, avoidable
  // render).
  const loadConnectedRepos = useCallback(() => {
    const id = ++connectedRequestId.current;
    return apiFetch<ConnectedRepository[]>("/api/repositories")
      .then((data) => {
        if (id === connectedRequestId.current) {
          setConnectedRepos(data);
          setConnectedError(null);
        }
      })
      .catch(() => {
        if (id === connectedRequestId.current) {
          setConnectedError("Couldn't load repositories, try again.");
        }
      })
      .finally(() => {
        if (id === connectedRequestId.current) {
          setConnectedLoading(false);
        }
      });
  }, []);

  // Public refetch: resets `connectedLoading` back to `true` before firing
  // a new request. Only ever called from event handlers, never an effect.
  const fetchConnectedRepos = useCallback(() => {
    setConnectedLoading(true);
    return loadConnectedRepos();
  }, [loadConnectedRepos]);

  // --- Live GitHub repos, fetched on demand (real GitHub API call) ---
  const [githubRepos, setGithubRepos] = useState<GithubRepoSummary[] | null>(
    null,
  );
  const [githubReposLoading, setGithubReposLoading] = useState(false);
  const [githubReposError, setGithubReposError] = useState<string | null>(
    null,
  );
  const [hasBrowsed, setHasBrowsed] = useState(false);

  const fetchGithubRepos = useCallback(async () => {
    setHasBrowsed(true);
    setGithubReposLoading(true);
    setGithubReposError(null);
    try {
      const data = await apiFetch<GithubRepoSummary[]>("/api/github/repos");
      setGithubRepos(data);
    } catch {
      setGithubReposError(
        "Couldn't load your GitHub repositories, try again.",
      );
    } finally {
      setGithubReposLoading(false);
    }
  }, []);

  // --- Connect action ---
  const [connectingId, setConnectingId] = useState<number | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);

  const handleConnect = useCallback(
    async (githubId: number) => {
      setConnectingId(githubId);
      setConnectError(null);
      try {
        await apiFetch<ConnectedRepository>("/api/repositories", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ github_id: githubId }),
        });
      } catch (error) {
        // 409 means it's already connected — most likely a race with
        // another tab/request. Not a real error: just resync below.
        if (!(error instanceof ApiError) || error.status !== 409) {
          setConnectError("Couldn't connect that repository, try again.");
        }
      } finally {
        await fetchConnectedRepos();
        setConnectingId(null);
      }
    },
    [fetchConnectedRepos],
  );

  // --- Index action ---
  // Repo ids with an index POST currently in flight — a `Set` rather than
  // a single id (unlike `connectingId` above) since indexing several repos
  // around the same time is a reasonable thing to do, and each row's
  // button must only reflect its own request.
  const [indexingRequestIds, setIndexingRequestIds] = useState<Set<string>>(
    new Set(),
  );
  const [indexError, setIndexError] = useState<string | null>(null);

  const handleIndexRepo = useCallback(
    async (repoId: string) => {
      setIndexingRequestIds((prev) => new Set(prev).add(repoId));
      setIndexError(null);
      try {
        const updated = await apiFetch<ConnectedRepository>(
          `/api/repositories/${repoId}/index`,
          { method: "POST" },
        );
        setConnectedRepos((prev) =>
          prev
            ? prev.map((repo) => (repo.id === repoId ? updated : repo))
            : prev,
        );
      } catch (error) {
        // 409 means it's already queued/indexing — most likely a race
        // with another tab/request. Not a real error: just resync so the
        // button/status reflect the real current state.
        if (error instanceof ApiError && error.status === 409) {
          await loadConnectedRepos();
        } else {
          setIndexError("Couldn't start indexing, try again.");
        }
      } finally {
        setIndexingRequestIds((prev) => {
          const next = new Set(prev);
          next.delete(repoId);
          return next;
        });
      }
    },
    [loadConnectedRepos],
  );

  // --- Logout ---
  const [loggingOut, setLoggingOut] = useState(false);

  const handleLogout = useCallback(async () => {
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      // Nothing useful to retry here — send the user back to the landing
      // page regardless of whether the request succeeded.
    } finally {
      router.replace("/");
    }
  }, [router]);

  // Protected route: bounce signed-out visitors back to the landing page.
  useEffect(() => {
    if (!userLoading && !user) {
      router.replace("/");
    }
  }, [userLoading, user, router]);

  // Load the connected list once we know the visitor is signed in.
  // `connectedLoading` already starts `true`, so this doesn't need to set
  // it again — keeps the effect's own body free of any synchronous
  // setState call.
  useEffect(() => {
    if (!userLoading && user) {
      loadConnectedRepos();
    }
  }, [userLoading, user, loadConnectedRepos]);

  // Derived from `connectedRepos` rather than read inline in the effect
  // below so that effect only re-runs (tearing down and rebuilding its
  // interval) when pending-ness actually flips, not on every poll tick's
  // own fetch.
  const hasPendingIndexing = useMemo(
    () =>
      (connectedRepos ?? []).some((repo) =>
        isPendingIndexingStatus(repo.indexing_status),
      ),
    [connectedRepos],
  );

  // Poll while anything is queued/indexing so status transitions (queued
  // -> indexing -> indexed/failed) show up without a manual refresh, and
  // stop as soon as nothing is pending. Uses `loadConnectedRepos` (not
  // `fetchConnectedRepos`) so a poll tick never flips `connectedLoading`
  // and flashes the list back to its loading state. The effect body only
  // schedules/clears a timer — no setState call runs synchronously in it.
  useEffect(() => {
    if (!hasPendingIndexing) {
      return;
    }
    const intervalId = setInterval(() => {
      loadConnectedRepos();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [hasPendingIndexing, loadConnectedRepos]);

  const connectedGithubIds = useMemo(
    () => new Set((connectedRepos ?? []).map((repo) => repo.github_id)),
    [connectedRepos],
  );

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center bg-zinc-50 px-6 py-16 dark:bg-black">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <header className="flex items-center justify-between border-b border-black/[.08] px-6 py-4 dark:border-white/[.145]">
        <div className="flex items-center gap-3">
          {user.avatar_url ? (
            <Image
              src={user.avatar_url}
              alt={`${user.username}'s avatar`}
              width={32}
              height={32}
              unoptimized
              className="rounded-full"
            />
          ) : null}
          <span className="text-sm font-medium text-zinc-950 dark:text-zinc-50">
            {user.username}
          </span>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          disabled={loggingOut}
          className="rounded-md border border-black/[.08] px-3 py-1.5 text-sm text-zinc-700 transition-colors hover:bg-black/[.04] disabled:opacity-50 dark:border-white/[.145] dark:text-zinc-300 dark:hover:bg-white/[.06]"
        >
          {loggingOut ? "Logging out…" : "Log out"}
        </button>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-10 px-6 py-10">
        <section className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
            Your repositories
          </h1>

          {indexError ? (
            <p className="text-sm text-red-600 dark:text-red-400">
              {indexError}
            </p>
          ) : null}

          {connectedLoading ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Loading repositories…
            </p>
          ) : connectedError ? (
            <div className="flex items-center gap-3">
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                {connectedError}
              </p>
              <button
                type="button"
                onClick={fetchConnectedRepos}
                className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
              >
                Try again
              </button>
            </div>
          ) : connectedRepos && connectedRepos.length > 0 ? (
            <ul className="flex flex-col gap-3">
              {connectedRepos.map((repo) => {
                const isRequesting = indexingRequestIds.has(repo.id);
                const isIndexDisabled =
                  isRequesting || isPendingIndexingStatus(repo.indexing_status);
                return (
                  <li
                    key={repo.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]"
                  >
                    <RepoMeta
                      fullName={repo.full_name}
                      description={repo.description}
                      language={repo.language}
                      isPrivate={repo.private}
                    />
                    <div className="flex shrink-0 flex-wrap items-center gap-3">
                      <IndexingStatus repo={repo} />
                      <button
                        type="button"
                        disabled={isIndexDisabled}
                        onClick={() => handleIndexRepo(repo.id)}
                        className="shrink-0 rounded-md border border-black/[.08] px-3 py-1.5 text-sm font-medium text-zinc-950 transition-colors hover:bg-black/[.04] disabled:cursor-default disabled:opacity-50 dark:border-white/[.145] dark:text-zinc-50 dark:hover:bg-white/[.06]"
                      >
                        {indexButtonLabel(repo.indexing_status, isRequesting)}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No repositories connected yet.
            </p>
          )}
        </section>

        <section className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
              Browse your GitHub repositories
            </h2>
            <button
              type="button"
              onClick={fetchGithubRepos}
              disabled={githubReposLoading}
              className="rounded-md bg-zinc-950 px-3 py-1.5 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
            >
              {githubReposLoading
                ? "Loading…"
                : hasBrowsed
                  ? "Refresh"
                  : "Browse your GitHub repositories"}
            </button>
          </div>

          {connectError ? (
            <p className="text-sm text-red-600 dark:text-red-400">
              {connectError}
            </p>
          ) : null}

          {githubReposLoading ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Fetching your repositories from GitHub — this can take a
              moment…
            </p>
          ) : githubReposError ? (
            <div className="flex items-center gap-3">
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                {githubReposError}
              </p>
              <button
                type="button"
                onClick={fetchGithubRepos}
                className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
              >
                Try again
              </button>
            </div>
          ) : githubRepos ? (
            githubRepos.length > 0 ? (
              <ul className="flex flex-col gap-3">
                {githubRepos.map((repo) => {
                  const isConnected = connectedGithubIds.has(repo.github_id);
                  const isConnecting = connectingId === repo.github_id;
                  return (
                    <li
                      key={repo.github_id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]"
                    >
                      <RepoMeta
                        fullName={repo.full_name}
                        description={repo.description}
                        language={repo.language}
                        isPrivate={repo.private}
                      />
                      <button
                        type="button"
                        disabled={isConnected || isConnecting}
                        onClick={() => handleConnect(repo.github_id)}
                        className="shrink-0 rounded-md border border-black/[.08] px-3 py-1.5 text-sm font-medium text-zinc-950 transition-colors hover:bg-black/[.04] disabled:cursor-default disabled:opacity-50 dark:border-white/[.145] dark:text-zinc-50 dark:hover:bg-white/[.06]"
                      >
                        {isConnected
                          ? "Connected"
                          : isConnecting
                            ? "Connecting…"
                            : "Connect"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                No repositories found on GitHub.
              </p>
            )
          ) : null}
        </section>
      </main>
    </div>
  );
}
