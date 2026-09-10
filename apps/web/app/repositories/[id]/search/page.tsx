"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import type { ConnectedRepository, SearchResult } from "@codepilot/shared-types";
import { apiFetch, ApiError } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

// Matches the backend's own default (see `search_code` in
// app/services/search.py) -- passed explicitly rather than leaning on the
// endpoint's default so this stays correct if that default ever changes.
const SEARCH_LIMIT = 20;

function Badge({ children }: { children: string }) {
  return (
    <span className="rounded-full bg-black/[.05] px-2 py-0.5 text-xs text-zinc-600 dark:bg-white/[.08] dark:text-zinc-400">
      {children}
    </span>
  );
}

// A repository is searchable once it has actually finished indexing and
// produced chunks. Mirrors `isRepoSearchable` on the repositories list page,
// which uses the identical check to enable/disable the "Search" button that
// links here.
function isSearchable(repo: ConnectedRepository): boolean {
  return repo.indexing_status === "indexed" && repo.chunk_count > 0;
}

function notSearchableMessage(repo: ConnectedRepository): string {
  switch (repo.indexing_status) {
    case "queued":
    case "indexing":
      return "This repository is still being indexed — check back in a bit.";
    case "failed":
      return "Indexing failed for this repository, so there's nothing to search yet.";
    default:
      return "This repository hasn't been indexed yet.";
  }
}

// Simple bar + percentage -- deliberately not a fancier gauge, per the
// "keep it simple" steer for the score indicator.
function ScoreIndicator({ score }: { score: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
  return (
    <div className="flex items-center gap-2" title={`Match score: ${pct}%`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-black/[.08] dark:bg-white/[.12]">
        <div
          className="h-full rounded-full bg-zinc-950 dark:bg-zinc-50"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-9 shrink-0 text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
        {pct}%
      </span>
    </div>
  );
}

function ResultCard({ result }: { result: SearchResult }) {
  return (
    <li className="flex flex-col gap-3 rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="break-all font-mono text-sm font-medium text-zinc-950 dark:text-zinc-50">
            {result.file_path}:{result.start_line}-{result.end_line}
          </span>
          <Badge>
            {result.symbol_name
              ? `${result.chunk_type} · ${result.symbol_name}`
              : result.chunk_type}
          </Badge>
          <Badge>{result.language}</Badge>
        </div>
        <ScoreIndicator score={result.score} />
      </div>
      <pre className="max-h-96 overflow-auto rounded-md bg-black/[.03] px-3 py-2 text-xs text-zinc-800 dark:bg-white/[.04] dark:text-zinc-200">
        <code>{result.content}</code>
      </pre>
    </li>
  );
}

function SearchPageContent() {
  const { id: repositoryId } = useParams<{ id: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const { user, loading: userLoading } = useCurrentUser();

  // --- Repository details: full_name for the header, indexing status to
  // know whether there's anything to search at all ---
  const [repo, setRepo] = useState<ConnectedRepository | null>(null);
  const [repoLoading, setRepoLoading] = useState(true);
  const [repoError, setRepoError] = useState<string | null>(null);
  const [repoNotFound, setRepoNotFound] = useState(false);

  // Guards against out-of-order responses, same pattern as
  // `connectedRequestId` in repositories/page.tsx.
  const repoRequestId = useRef(0);

  // Fires the request and settles state via `.then/.catch/.finally` only --
  // no setState call runs synchronously in this function's own body, so
  // it's safe to invoke directly from an effect.
  const loadRepo = useCallback(() => {
    const id = ++repoRequestId.current;
    return apiFetch<ConnectedRepository>(`/api/repositories/${repositoryId}`)
      .then((data) => {
        if (id === repoRequestId.current) {
          setRepo(data);
          setRepoError(null);
          setRepoNotFound(false);
        }
      })
      .catch((error) => {
        if (id !== repoRequestId.current) {
          return;
        }
        // 404 means the repo doesn't exist or isn't owned by this user --
        // not something "Try again" can fix, so it gets its own message
        // and a way back instead of a retry button.
        if (error instanceof ApiError && error.status === 404) {
          setRepoNotFound(true);
          setRepoError("Repository not found.");
        } else {
          setRepoNotFound(false);
          setRepoError("Couldn't load this repository, try again.");
        }
      })
      .finally(() => {
        if (id === repoRequestId.current) {
          setRepoLoading(false);
        }
      });
  }, [repositoryId]);

  // Public refetch: resets `repoLoading` back to `true` before firing a new
  // request. Only ever called from an event handler, never an effect.
  const fetchRepo = useCallback(() => {
    setRepoLoading(true);
    return loadRepo();
  }, [loadRepo]);

  // --- Search ---
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Guards against out-of-order responses -- pressing Enter twice in quick
  // succession (or submit then "Try again") must not let a slower earlier
  // response clobber a newer one. Same pattern as `connectedRequestId`.
  const searchRequestId = useRef(0);

  const runSearch = useCallback(
    (rawQuery: string) => {
      const trimmed = rawQuery.trim();
      if (!trimmed) {
        return;
      }
      const id = ++searchRequestId.current;
      setSearchLoading(true);
      setSearchError(null);

      const qs = new URLSearchParams({ q: trimmed, limit: String(SEARCH_LIMIT) });
      apiFetch<SearchResult[]>(`/api/repositories/${repositoryId}/search?${qs.toString()}`)
        .then((data) => {
          if (id === searchRequestId.current) {
            setResults(data);
            setSearchedQuery(trimmed);
          }
        })
        .catch((error) => {
          if (id === searchRequestId.current) {
            // The endpoint is rate-limited to 30 req/min per user -- call
            // that out specifically rather than a generic failure message.
            setSearchError(
              error instanceof ApiError && error.status === 429
                ? "Too many searches — wait a moment and try again."
                : "Couldn't run that search, try again.",
            );
          }
        })
        .finally(() => {
          if (id === searchRequestId.current) {
            setSearchLoading(false);
          }
        });

      // Keep the URL in sync so a search is bookmarkable/shareable and
      // survives a refresh (re-run further below, once the repo confirms
      // searchable).
      const urlParams = new URLSearchParams({ q: trimmed });
      router.replace(`${pathname}?${urlParams.toString()}`);
    },
    [repositoryId, pathname, router],
  );

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      runSearch(query);
    },
    [query, runSearch],
  );

  // Protected route: bounce signed-out visitors back to the landing page.
  useEffect(() => {
    if (!userLoading && !user) {
      router.replace("/");
    }
  }, [userLoading, user, router]);

  // Load the repository once we know the visitor is signed in.
  // `repoLoading` already starts `true`, so this doesn't need to set it
  // again -- keeps the effect's own body free of any synchronous setState
  // call.
  useEffect(() => {
    if (!userLoading && user) {
      loadRepo();
    }
  }, [userLoading, user, loadRepo]);

  // A `q` present in the URL on load (a shared link, or a refresh) is
  // auto-run once the repo confirms searchable. Read once up front into a
  // ref rather than reactively off `searchParams` so `runSearch`'s own
  // `router.replace` call (which changes `searchParams`) can't re-arm this;
  // `hasAutoSearched` guards it from firing more than once regardless.
  const initialQuery = useRef(searchParams.get("q"));
  const hasAutoSearched = useRef(false);
  useEffect(() => {
    if (hasAutoSearched.current || !repo || !isSearchable(repo)) {
      return;
    }
    hasAutoSearched.current = true;
    if (initialQuery.current?.trim()) {
      runSearch(initialQuery.current);
    }
  }, [repo, runSearch]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center bg-zinc-50 px-6 py-16 dark:bg-black">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <header className="flex items-center gap-3 border-b border-black/[.08] px-6 py-4 dark:border-white/[.145]">
        <Link
          href="/repositories"
          className="text-sm font-medium text-zinc-600 underline underline-offset-2 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-zinc-50"
        >
          ← Repositories
        </Link>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-6 py-10">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
          {repo ? `Search ${repo.full_name}` : "Search"}
        </h1>

        {repoLoading ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Loading repository…
          </p>
        ) : repoError ? (
          <div className="flex items-center gap-3">
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {repoError}
            </p>
            {repoNotFound ? (
              <Link
                href="/repositories"
                className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
              >
                Back to repositories
              </Link>
            ) : (
              <button
                type="button"
                onClick={fetchRepo}
                className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
              >
                Try again
              </button>
            )}
          </div>
        ) : repo && !isSearchable(repo) ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {notSearchableMessage(repo)}
            </p>
            <Link
              href="/repositories"
              className="w-fit text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
            >
              Back to repositories
            </Link>
          </div>
        ) : repo ? (
          <>
            <form onSubmit={handleSubmit} className="flex items-center gap-3">
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search this repository's code…"
                aria-label="Search this repository's code"
                maxLength={500}
                className="flex-1 rounded-md border border-black/[.08] bg-white px-3 py-2 text-sm text-zinc-950 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-950/20 dark:border-white/[.145] dark:bg-zinc-900 dark:text-zinc-50 dark:focus:ring-zinc-50/20"
              />
              <button
                type="submit"
                disabled={searchLoading || query.trim().length === 0}
                className="shrink-0 rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:cursor-default disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
              >
                {searchLoading ? "Searching…" : "Search"}
              </button>
            </form>

            {searchLoading ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                Searching…
              </p>
            ) : searchError ? (
              <div className="flex items-center gap-3">
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {searchError}
                </p>
                <button
                  type="button"
                  onClick={() => runSearch(query)}
                  className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
                >
                  Try again
                </button>
              </div>
            ) : results === null ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                Search this repository&apos;s indexed code by symbol name,
                behavior, or concept.
              </p>
            ) : results.length === 0 ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                No results found for &ldquo;{searchedQuery}&rdquo;.
              </p>
            ) : (
              <ul className="flex flex-col gap-3">
                {results.map((result) => (
                  <ResultCard key={result.chunk_id} result={result} />
                ))}
              </ul>
            )}
          </>
        ) : null}
      </main>
    </div>
  );
}

export default function RepositorySearchPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center bg-zinc-50 px-6 py-16 dark:bg-black">
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
        </div>
      }
    >
      <SearchPageContent />
    </Suspense>
  );
}
