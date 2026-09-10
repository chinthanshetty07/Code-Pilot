"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import type { ConnectedRepository, Issue } from "@codepilot/shared-types";
import { apiFetch, ApiError } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

// How often to re-fetch the issues list while one is queued/planning. Same
// interval as the repositories page's indexing poll (`POLL_INTERVAL_MS` in
// repositories/page.tsx).
const POLL_INTERVAL_MS = 2500;

// Matches the backend's own bound (see `IssueCreateIn` in
// app/schemas/issue.py) -- enforced here too so the textarea and submit
// button give instant feedback instead of waiting on a 422.
const MAX_DESCRIPTION_LENGTH = 5000;

const MAX_ROW_TEXT_LENGTH = 220;

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

// Small relative-time formatter built on the native `Intl` API. Copied
// verbatim from repositories/page.tsx -- see that file for why the locale
// is pinned (not the runtime default): this page is SSR'd on first load
// like any client component, and an unpinned locale renders differently on
// the server than in the browser, producing a hydration mismatch.
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

// Absolute-time label for the `title` tooltip. Locale and time zone are both
// pinned for the same reason as `formatRelativeTime` above. Copied verbatim
// from repositories/page.tsx.
function formatAbsoluteTime(iso: string): string {
  const formatted = new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  });
  return `${formatted} UTC`;
}

// A repository is ready for planning once it has actually finished indexing
// and produced chunks -- the Planner agent's `search_code` tool needs
// chunks to search, exactly like a manual search does. Same condition
// (`isSearchable`) and same copy (`notSearchableMessage`) as
// repositories/[id]/search/page.tsx, kept verbatim on purpose: a user
// bouncing between Search and Issues on a not-yet-indexed repo should see
// an identical message, not a differently-worded one that happens to mean
// the same thing.
function isPlannable(repo: ConnectedRepository): boolean {
  return repo.indexing_status === "indexed" && repo.chunk_count > 0;
}

function notPlannableMessage(repo: ConnectedRepository): string {
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

// Statuses that mean "the worker is on it" -- while an issue is in one of
// these, the page keeps polling for updates. Same convention as
// `PENDING_INDEXING_STATUSES` in repositories/page.tsx.
const PENDING_PLANNING_STATUSES = new Set(["queued", "planning"]);

function isPendingPlanningStatus(status: string): boolean {
  return PENDING_PLANNING_STATUSES.has(status);
}

// Dot color + label per `planning_status`, same convention as
// repositories/page.tsx's `STATUS_DOT_CLASS`/`IndexingStatus`: amber while
// waiting, pulsing amber while active, emerald on success, red on failure.
const PLANNING_STATUS_DOT_CLASS: Record<string, string> = {
  queued: "bg-amber-400",
  planning: "bg-amber-400 animate-pulse",
  planned: "bg-emerald-500",
  failed: "bg-red-500",
};

const PLANNING_STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  planning: "Planning…",
  planned: "Planned",
  failed: "Failed",
};

function PlanningStatusBadge({ status }: { status: string }) {
  const dotClass = PLANNING_STATUS_DOT_CLASS[status];
  return (
    <div
      role="status"
      className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400"
    >
      {dotClass ? (
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`} />
      ) : null}
      <span>{PLANNING_STATUS_LABEL[status] ?? status}</span>
    </div>
  );
}

function IssueRow({
  issue,
  repositoryId,
}: {
  issue: Issue;
  repositoryId: string;
}) {
  const isPlanned = issue.planning_status === "planned";

  const content = (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p
          className="max-w-2xl whitespace-pre-wrap break-words text-sm text-zinc-950 dark:text-zinc-50"
          title={
            issue.description.length > MAX_ROW_TEXT_LENGTH
              ? issue.description
              : undefined
          }
        >
          {truncate(issue.description, MAX_ROW_TEXT_LENGTH)}
        </p>
        {isPlanned ? (
          <span className="shrink-0 text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50">
            View plan →
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <PlanningStatusBadge status={issue.planning_status} />
        <span
          className="text-xs text-zinc-500 dark:text-zinc-400"
          title={formatAbsoluteTime(issue.created_at)}
          suppressHydrationWarning
        >
          {formatRelativeTime(issue.created_at)}
        </span>
      </div>
      {issue.planning_status === "failed" ? (
        <p
          className="break-words text-sm text-red-600 dark:text-red-400"
          title={issue.planning_error ?? undefined}
        >
          {issue.planning_error
            ? truncate(issue.planning_error, MAX_ROW_TEXT_LENGTH)
            : "Planning failed."}
        </p>
      ) : null}
    </div>
  );

  if (isPlanned) {
    return (
      <li>
        <Link
          href={`/repositories/${repositoryId}/issues/${issue.id}`}
          className="block rounded-lg border border-black/[.08] px-4 py-3 transition-colors hover:bg-black/[.04] dark:border-white/[.145] dark:hover:bg-white/[.06]"
        >
          {content}
        </Link>
      </li>
    );
  }

  return (
    <li className="rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]">
      {content}
    </li>
  );
}

export default function IssuesPage() {
  const { id: repositoryId } = useParams<{ id: string }>();
  const router = useRouter();
  const { user, loading: userLoading } = useCurrentUser();

  // --- Repository details: full_name for the header, indexing status to
  // know whether planning is possible at all ---
  const [repo, setRepo] = useState<ConnectedRepository | null>(null);
  const [repoLoading, setRepoLoading] = useState(true);
  const [repoError, setRepoError] = useState<string | null>(null);
  const [repoNotFound, setRepoNotFound] = useState(false);

  // Guards against out-of-order responses, same pattern as
  // `connectedRequestId` in repositories/page.tsx.
  const repoRequestId = useRef(0);

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

  const fetchRepo = useCallback(() => {
    setRepoLoading(true);
    return loadRepo();
  }, [loadRepo]);

  // --- Issues list ---
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [issuesLoading, setIssuesLoading] = useState(true);
  const [issuesError, setIssuesError] = useState<string | null>(null);

  // Guards against out-of-order responses, same pattern as above.
  const issuesRequestId = useRef(0);

  // Fires the request and settles state via `.then/.catch/.finally` only --
  // no setState call runs synchronously in this function's own body, so
  // it's safe to invoke directly from an effect or from the poll interval.
  const loadIssues = useCallback(() => {
    const id = ++issuesRequestId.current;
    return apiFetch<Issue[]>(`/api/repositories/${repositoryId}/issues`)
      .then((data) => {
        if (id === issuesRequestId.current) {
          setIssues(data);
          setIssuesError(null);
        }
      })
      .catch(() => {
        if (id === issuesRequestId.current) {
          setIssuesError("Couldn't load issues, try again.");
        }
      })
      .finally(() => {
        if (id === issuesRequestId.current) {
          setIssuesLoading(false);
        }
      });
  }, [repositoryId]);

  const fetchIssues = useCallback(() => {
    setIssuesLoading(true);
    return loadIssues();
  }, [loadIssues]);

  // --- Create issue form ---
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmed = description.trim();
      if (trimmed.length === 0 || trimmed.length > MAX_DESCRIPTION_LENGTH) {
        return;
      }
      setSubmitting(true);
      setSubmitError(null);
      try {
        const created = await apiFetch<Issue>(
          `/api/repositories/${repositoryId}/issues`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ description: trimmed }),
          },
        );
        // Prepend rather than refetching the whole list -- the new issue is
        // "queued", and `hasPendingPlanning` below picks that up on its own
        // to start polling.
        setIssues((prev) => (prev ? [created, ...prev] : [created]));
        setDescription("");
      } catch (error) {
        // The endpoint is rate-limited to 10 req/min -- call that out
        // specifically, same convention as the search page's 429 handling.
        setSubmitError(
          error instanceof ApiError && error.status === 429
            ? "Too many issues submitted — wait a moment and try again."
            : "Couldn't submit that issue, try again.",
        );
      } finally {
        setSubmitting(false);
      }
    },
    [description, repositoryId],
  );

  // Protected route: bounce signed-out visitors back to the landing page.
  useEffect(() => {
    if (!userLoading && !user) {
      router.replace("/");
    }
  }, [userLoading, user, router]);

  // Load the repo and the issues list once we know the visitor is signed
  // in. These are independent resources, so they fire in parallel rather
  // than waiting on each other -- `issues` simply isn't rendered unless
  // `repo` turns out to be plannable. `repoLoading`/`issuesLoading` already
  // start `true`, so this doesn't need to set either again.
  useEffect(() => {
    if (!userLoading && user) {
      loadRepo();
      loadIssues();
    }
  }, [userLoading, user, loadRepo, loadIssues]);

  // Derived from `issues` rather than read inline in the effect below so
  // that effect only re-runs (tearing down and rebuilding its interval)
  // when pending-ness actually flips, not on every poll tick's own fetch.
  const hasPendingPlanning = useMemo(
    () =>
      (issues ?? []).some((issue) => isPendingPlanningStatus(issue.planning_status)),
    [issues],
  );

  // Poll while anything is queued/planning so status transitions show up
  // without a manual refresh, and stop as soon as nothing is pending. Same
  // pattern as the indexing poll in repositories/page.tsx: uses
  // `loadIssues` (not `fetchIssues`) so a poll tick never flips
  // `issuesLoading` and flashes the list back to its loading state.
  useEffect(() => {
    if (!hasPendingPlanning) {
      return;
    }
    const intervalId = setInterval(() => {
      loadIssues();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [hasPendingPlanning, loadIssues]);

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

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-10 px-6 py-10">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
          {repo ? `Issues · ${repo.full_name}` : "Issues"}
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
        ) : repo && !isPlannable(repo) ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {notPlannableMessage(repo)}
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
            <section className="flex flex-col gap-4">
              <h2 className="text-lg font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
                New issue
              </h2>
              <form onSubmit={handleSubmit} className="flex flex-col gap-2">
                <label
                  htmlFor="issue-description"
                  className="text-sm font-medium text-zinc-950 dark:text-zinc-50"
                >
                  Describe an issue
                </label>
                <textarea
                  id="issue-description"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="What's going wrong, and where have you noticed it? The more detail, the better the plan."
                  maxLength={MAX_DESCRIPTION_LENGTH}
                  rows={4}
                  className="resize-y rounded-md border border-black/[.08] bg-white px-3 py-2 text-sm text-zinc-950 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-950/20 dark:border-white/[.145] dark:bg-zinc-900 dark:text-zinc-50 dark:focus:ring-zinc-50/20"
                />
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    {description.length} / {MAX_DESCRIPTION_LENGTH}
                  </span>
                  <button
                    type="submit"
                    disabled={submitting || description.trim().length === 0}
                    className="shrink-0 rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:cursor-default disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
                  >
                    {submitting ? "Submitting…" : "Submit issue"}
                  </button>
                </div>
                {submitError ? (
                  <p className="text-sm text-red-600 dark:text-red-400">
                    {submitError}
                  </p>
                ) : null}
              </form>
            </section>

            <section className="flex flex-col gap-4">
              <h2 className="text-lg font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
                Issues
              </h2>
              {issuesLoading ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading issues…
                </p>
              ) : issuesError ? (
                <div className="flex items-center gap-3">
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">
                    {issuesError}
                  </p>
                  <button
                    type="button"
                    onClick={fetchIssues}
                    className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
                  >
                    Try again
                  </button>
                </div>
              ) : issues && issues.length > 0 ? (
                <ul className="flex flex-col gap-3">
                  {issues.map((issue) => (
                    <IssueRow
                      key={issue.id}
                      issue={issue}
                      repositoryId={repositoryId}
                    />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No issues submitted yet.
                </p>
              )}
            </section>
          </>
        ) : null}
      </main>
    </div>
  );
}
