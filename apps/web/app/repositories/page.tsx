"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  extraBadge,
}: {
  fullName: string;
  description: string | null;
  language: string | null;
  isPrivate: boolean;
  extraBadge?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-zinc-950 dark:text-zinc-50">
          {fullName}
        </span>
        <Badge>{isPrivate ? "Private" : "Public"}</Badge>
        {language ? <Badge>{language}</Badge> : null}
        {extraBadge ? <Badge>{extraBadge}</Badge> : null}
      </div>
      {description ? (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {description}
        </p>
      ) : null}
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

  // Fires the request and settles state via `.then/.catch/.finally` only —
  // no setState call runs synchronously in this function's own body, so
  // it's safe to invoke directly from an effect (calling something that
  // sets state synchronously inside an effect causes an extra, avoidable
  // render).
  const loadConnectedRepos = useCallback(() => {
    return apiFetch<ConnectedRepository[]>("/api/repositories")
      .then((data) => {
        setConnectedRepos(data);
        setConnectedError(null);
      })
      .catch(() => {
        setConnectedError("Couldn't load repositories, try again.");
      })
      .finally(() => {
        setConnectedLoading(false);
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
              {connectedRepos.map((repo) => (
                <li
                  key={repo.id}
                  className="rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]"
                >
                  <RepoMeta
                    fullName={repo.full_name}
                    description={repo.description}
                    language={repo.language}
                    isPrivate={repo.private}
                    extraBadge={repo.indexing_status}
                  />
                </li>
              ))}
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
