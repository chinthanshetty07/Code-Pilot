"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import type { Issue, Plan } from "@codepilot/shared-types";
import { apiFetch, ApiError } from "@/lib/api";
import { useCurrentUser } from "@/hooks/use-current-user";

// How often to re-fetch the issue while it's queued/planning. Same interval
// as the poll on the issues list page and the indexing poll on
// repositories/page.tsx.
const POLL_INTERVAL_MS = 2500;

// Statuses that mean "the worker is on it" -- copied verbatim from
// repositories/[id]/issues/page.tsx so both pages treat the same set of
// statuses as pending.
const PENDING_PLANNING_STATUSES = new Set(["queued", "planning"]);

function isPendingPlanningStatus(status: string): boolean {
  return PENDING_PLANNING_STATUSES.has(status);
}

// Dot color + label per `planning_status`. Copied verbatim from
// repositories/[id]/issues/page.tsx -- see that file for the convention
// this follows (repositories/page.tsx's `STATUS_DOT_CLASS`/`IndexingStatus`).
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

function SectionHeading({ children }: { children: string }) {
  return (
    <h2 className="text-lg font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
      {children}
    </h2>
  );
}

function PlanView({ plan }: { plan: Plan }) {
  return (
    <div className="flex flex-col gap-6">
      <p className="text-base text-zinc-950 dark:text-zinc-50">{plan.summary}</p>

      <div className="flex flex-col gap-2">
        <SectionHeading>Relevant files</SectionHeading>
        {plan.relevant_files.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {plan.relevant_files.map((file, index) => (
              <li
                key={`${file.file_path}-${index}`}
                className="rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]"
              >
                <div className="break-all font-mono text-sm font-medium text-zinc-950 dark:text-zinc-50">
                  {file.file_path}
                </div>
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                  {file.reason}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No specific files identified.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <SectionHeading>Implementation steps</SectionHeading>
        {plan.implementation_steps.length > 0 ? (
          <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-zinc-800 dark:text-zinc-200">
            {plan.implementation_steps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No implementation steps provided.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <SectionHeading>Tests to add or change</SectionHeading>
        {plan.tests_to_add_or_change.length > 0 ? (
          <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-zinc-800 dark:text-zinc-200">
            {plan.tests_to_add_or_change.map((test, index) => (
              <li key={index}>{test}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No test changes suggested.
          </p>
        )}
      </div>

      {/* Visually distinct (amber, left-border accent) rather than the
          neutral styling above -- these are things to be careful about,
          not just more plan content. */}
      <div className="flex flex-col gap-2 rounded-lg border border-amber-200 border-l-4 border-l-amber-400 bg-amber-50 px-4 py-3 dark:border-amber-900/40 dark:border-l-amber-500 dark:bg-amber-950/20">
        <h2 className="text-lg font-semibold tracking-tight text-amber-900 dark:text-amber-400">
          Risks
        </h2>
        {plan.risks.length > 0 ? (
          <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-amber-900 dark:text-amber-300">
            {plan.risks.map((risk, index) => (
              <li key={index}>{risk}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-amber-800 dark:text-amber-400">
            No specific risks called out.
          </p>
        )}
      </div>
    </div>
  );
}

export default function IssueDetailPage() {
  const { id: repositoryId, issueId } = useParams<{
    id: string;
    issueId: string;
  }>();
  const router = useRouter();
  const { user, loading: userLoading } = useCurrentUser();

  const [issue, setIssue] = useState<Issue | null>(null);
  const [issueLoading, setIssueLoading] = useState(true);
  const [issueError, setIssueError] = useState<string | null>(null);
  const [issueNotFound, setIssueNotFound] = useState(false);

  // Guards against out-of-order responses, same pattern as `repoRequestId`
  // in repositories/[id]/issues/page.tsx.
  const issueRequestId = useRef(0);

  // Fires the request and settles state via `.then/.catch/.finally` only --
  // no setState call runs synchronously in this function's own body, so
  // it's safe to invoke directly from an effect or from the poll interval.
  const loadIssue = useCallback(() => {
    const id = ++issueRequestId.current;
    return apiFetch<Issue>(
      `/api/repositories/${repositoryId}/issues/${issueId}`,
    )
      .then((data) => {
        if (id === issueRequestId.current) {
          setIssue(data);
          setIssueError(null);
          setIssueNotFound(false);
        }
      })
      .catch((error) => {
        if (id !== issueRequestId.current) {
          return;
        }
        // 404 means the issue doesn't exist (or isn't owned by this user,
        // or doesn't belong to this repository) -- not something "Try
        // again" can fix, so it gets its own message and a way back.
        if (error instanceof ApiError && error.status === 404) {
          setIssueNotFound(true);
          setIssueError("Issue not found.");
        } else {
          setIssueNotFound(false);
          setIssueError("Couldn't load this issue, try again.");
        }
      })
      .finally(() => {
        if (id === issueRequestId.current) {
          setIssueLoading(false);
        }
      });
  }, [repositoryId, issueId]);

  const fetchIssue = useCallback(() => {
    setIssueLoading(true);
    return loadIssue();
  }, [loadIssue]);

  // Protected route: bounce signed-out visitors back to the landing page.
  useEffect(() => {
    if (!userLoading && !user) {
      router.replace("/");
    }
  }, [userLoading, user, router]);

  // Load the issue once we know the visitor is signed in. `issueLoading`
  // already starts `true`, so this doesn't need to set it again -- keeps
  // the effect's own body free of any synchronous setState call. This page
  // is reachable directly (a bookmarked/shared link), so it can't assume
  // the issue is already planned by the time it loads.
  useEffect(() => {
    if (!userLoading && user) {
      loadIssue();
    }
  }, [userLoading, user, loadIssue]);

  const isPending = issue ? isPendingPlanningStatus(issue.planning_status) : false;

  // Poll while the issue is queued/planning so it resolves to planned/failed
  // without a manual refresh, and stop as soon as it does. Same pattern as
  // the list page's poll, scoped to this one issue instead of a list.
  useEffect(() => {
    if (!isPending) {
      return;
    }
    const intervalId = setInterval(() => {
      loadIssue();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [isPending, loadIssue]);

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
          href={`/repositories/${repositoryId}/issues`}
          className="text-sm font-medium text-zinc-600 underline underline-offset-2 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-zinc-50"
        >
          ← Issues
        </Link>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-6 py-10">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
          Implementation plan
        </h1>

        {issueLoading ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Loading issue…
          </p>
        ) : issueError ? (
          <div className="flex items-center gap-3">
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {issueError}
            </p>
            {issueNotFound ? (
              <Link
                href={`/repositories/${repositoryId}/issues`}
                className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
              >
                Back to issues
              </Link>
            ) : (
              <button
                type="button"
                onClick={fetchIssue}
                className="text-sm font-medium text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
              >
                Try again
              </button>
            )}
          </div>
        ) : issue ? (
          <>
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  aria-hidden
                  className={`h-2 w-2 shrink-0 rounded-full ${
                    PLANNING_STATUS_DOT_CLASS[issue.planning_status] ?? ""
                  }`}
                />
                <span className="text-sm text-zinc-600 dark:text-zinc-400">
                  {PLANNING_STATUS_LABEL[issue.planning_status] ??
                    issue.planning_status}
                  {" · "}
                  <span
                    title={formatAbsoluteTime(issue.created_at)}
                    suppressHydrationWarning
                  >
                    {formatRelativeTime(issue.created_at)}
                  </span>
                </span>
              </div>
              <p className="whitespace-pre-wrap break-words rounded-lg border border-black/[.08] bg-white px-4 py-3 text-sm text-zinc-950 dark:border-white/[.145] dark:bg-zinc-900 dark:text-zinc-50">
                {issue.description}
              </p>
            </div>

            {issue.planning_status === "failed" ? (
              <div className="flex flex-col gap-2">
                <SectionHeading>Planning failed</SectionHeading>
                <p className="whitespace-pre-wrap break-words rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400">
                  {issue.planning_error ??
                    "Something went wrong while planning this issue."}
                </p>
              </div>
            ) : isPending ? (
              <div
                role="status"
                className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400"
              >
                <span
                  aria-hidden
                  className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-400"
                />
                <span>
                  {issue.planning_status === "queued"
                    ? "Queued for planning…"
                    : "Planning…"}{" "}
                  This usually takes 10–30 seconds.
                </span>
              </div>
            ) : issue.plan ? (
              <PlanView plan={issue.plan} />
            ) : (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                No plan is available for this issue.
              </p>
            )}
          </>
        ) : null}
      </main>
    </div>
  );
}
