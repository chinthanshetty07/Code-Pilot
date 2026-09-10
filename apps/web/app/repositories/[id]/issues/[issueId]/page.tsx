"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import type {
  CodeChange,
  Issue,
  Plan,
  PullRequest,
  Review,
  TestRun,
  UsageSummary,
} from "@codepilot/shared-types";
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

// Statuses that mean "the Coder agent is on it" -- same idea as
// `PENDING_PLANNING_STATUSES` above, scoped to `code_change.generation_status`
// instead of `issue.planning_status`.
const PENDING_GENERATION_STATUSES = new Set(["queued", "generating"]);

function isPendingGenerationStatus(status: string): boolean {
  return PENDING_GENERATION_STATUSES.has(status);
}

// Dot color + label per `generation_status`. Exact same convention as
// `PLANNING_STATUS_DOT_CLASS`/`PLANNING_STATUS_LABEL` above, just for the
// Coder agent's status instead of the Planner's.
const GENERATION_STATUS_DOT_CLASS: Record<string, string> = {
  queued: "bg-amber-400",
  generating: "bg-amber-400 animate-pulse",
  generated: "bg-emerald-500",
  failed: "bg-red-500",
};

const GENERATION_STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  generating: "Generating…",
  generated: "Generated",
  failed: "Failed",
};

// Statuses that mean "the sandbox is on it" -- same idea as
// `PENDING_GENERATION_STATUSES` above, scoped to `test_run.status`. "fixing"
// counts as pending too: it's the Milestone 8 fix loop actually working
// (the Coder agent generating a fix, in between two "running"s), not a
// resting state.
const PENDING_TEST_RUN_STATUSES = new Set(["queued", "running", "fixing"]);

function isPendingTestRunStatus(status: string): boolean {
  return PENDING_TEST_RUN_STATUSES.has(status);
}

// Must match app/services/test_runner.py's MAX_FIX_ATTEMPTS -- there's no
// API-level source of truth for this one number, so it's just kept in sync
// by convention, the same loose coupling this page already has with the
// backend's status string unions (see the comments on TestRun in
// packages/shared-types/src/index.ts).
const MAX_FIX_ATTEMPTS = 3;

// Dot color + label per `test_run.status`. Same convention as
// `GENERATION_STATUS_DOT_CLASS`/`GENERATION_STATUS_LABEL` above, but with a
// three-way terminal split instead of two: "passed" is the usual emerald
// success, "failed" is the usual red failure (tests ran, some assertions
// didn't pass -- actionable, e.g. by the fix loop below), and "error" gets
// its own amber/orange tone rather than reusing either -- no verdict was
// reached at all (no test command detected, a sandbox/infra problem, or a
// timeout), which is a materially different situation from "failed" and
// shouldn't read as the same red box. Orange rather than plain amber
// specifically so it doesn't get confused with this same page's amber
// "queued/in progress" dots when skimming. "fixing" reuses the same pulsing
// amber as "running" -- it's the same kind of "in progress" as far as the
// dot is concerned, just with a more specific label.
const TEST_RUN_STATUS_DOT_CLASS: Record<string, string> = {
  queued: "bg-amber-400",
  running: "bg-amber-400 animate-pulse",
  fixing: "bg-amber-400 animate-pulse",
  passed: "bg-emerald-500",
  failed: "bg-red-500",
  error: "bg-orange-500",
};

const TEST_RUN_STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  running: "Running…",
  fixing: "Fixing…",
  passed: "Passed",
  failed: "Failed",
  error: "Error",
};

// Statuses that mean "the Reviewer agent is on it" -- same idea as
// `PENDING_TEST_RUN_STATUSES` above, scoped to `review.status`.
const PENDING_REVIEW_STATUSES = new Set(["queued", "reviewing"]);

function isPendingReviewStatus(status: string): boolean {
  return PENDING_REVIEW_STATUSES.has(status);
}

// Dot color + label per `review.status`. Same convention as
// `TEST_RUN_STATUS_DOT_CLASS`/`TEST_RUN_STATUS_LABEL` above, with its own
// three-way terminal split: "approved" is the usual emerald success,
// "failed" is the usual red (the review *process* broke -- an LLM/provider
// error, not a verdict), and "changes_requested" deliberately isn't amber
// (already this page's "in progress" color, see TEST_RUN_STATUS_DOT_CLASS's
// comment) or red (it's not a failure, it's the normal, expected outcome of
// a real review finding something worth fixing) -- sky reads as
// "constructive, look at this" without either connotation.
const REVIEW_STATUS_DOT_CLASS: Record<string, string> = {
  queued: "bg-amber-400",
  reviewing: "bg-amber-400 animate-pulse",
  approved: "bg-emerald-500",
  changes_requested: "bg-sky-500",
  failed: "bg-red-500",
};

const REVIEW_STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  reviewing: "Reviewing…",
  approved: "Approved",
  changes_requested: "Changes requested",
  failed: "Failed",
};

// Statuses that mean "CodePilot is pushing/opening it" -- same idea as
// `PENDING_REVIEW_STATUSES` above, scoped to `pull_request.status`.
const PENDING_PR_STATUSES = new Set(["queued", "creating"]);

function isPendingPRStatus(status: string): boolean {
  return PENDING_PR_STATUSES.has(status);
}

// Dot color + label per `pull_request.status`. Same convention as
// `REVIEW_STATUS_DOT_CLASS`/`REVIEW_STATUS_LABEL` above, but with only one
// terminal failure state instead of a three-way split: unlike a test run
// or a review, there's no equivalent of "ran, but the verdict was
// negative" for opening a PR -- it either genuinely exists on GitHub now
// ("created", emerald) or the attempt didn't produce one ("failed", red).
const PR_STATUS_DOT_CLASS: Record<string, string> = {
  queued: "bg-amber-400",
  creating: "bg-amber-400 animate-pulse",
  created: "bg-emerald-500",
  failed: "bg-red-500",
};

const PR_STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  creating: "Creating…",
  created: "Created",
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

// Button label for the generate/regenerate action, mirroring
// `indexButtonLabel`'s convention on repositories/page.tsx: reflects the
// in-flight POST first, then falls back to the persisted status.
function generateButtonLabel(
  codeChange: CodeChange | null,
  isRequesting: boolean,
): string {
  if (isRequesting) {
    return "Starting…";
  }
  if (!codeChange) {
    return "Generate code";
  }
  switch (codeChange.generation_status) {
    case "queued":
      return "Queued…";
    case "generating":
      return "Generating…";
    case "generated":
    case "failed":
      return "Regenerate";
    default:
      return "Generate code";
  }
}

// Button label for the run/re-run test action. Same convention as
// `generateButtonLabel` above, just scoped to `test_run.status` -- with all
// three terminal statuses (passed/failed/error) treated the same way here,
// since "re-run" is the right label regardless of which terminal state the
// previous attempt landed in.
function testRunButtonLabel(
  testRun: TestRun | null,
  isRequesting: boolean,
): string {
  if (isRequesting) {
    return "Starting…";
  }
  if (!testRun) {
    return "Run tests";
  }
  switch (testRun.status) {
    case "queued":
      return "Queued…";
    case "running":
      return "Running…";
    case "fixing":
      return "Fixing…";
    case "passed":
    case "failed":
    case "error":
      return "Run tests again";
    default:
      return "Run tests";
  }
}

// Button label for the request/re-request review action. Same convention
// as `testRunButtonLabel` above, with all three terminal statuses
// (approved/changes_requested/failed) treated the same way, since
// "re-request" is the right label regardless of which terminal state the
// previous review landed in.
function reviewButtonLabel(review: Review | null, isRequesting: boolean): string {
  if (isRequesting) {
    return "Starting…";
  }
  if (!review) {
    return "Request review";
  }
  switch (review.status) {
    case "queued":
      return "Queued…";
    case "reviewing":
      return "Reviewing…";
    case "approved":
    case "changes_requested":
    case "failed":
      return "Request review again";
    default:
      return "Request review";
  }
}

// Button label for the create-pull-request action. Unlike the other three
// button-label functions above, this is never called for a "created"
// pull_request -- the main component renders a "View pull request" link
// in its place instead of a clickable button once a real PR exists (see
// PullRequest's own status docstring for why that's a true terminal
// state, not a replaceable one like the others).
function pullRequestButtonLabel(
  pullRequest: PullRequest | null,
  isRequesting: boolean,
): string {
  if (isRequesting) {
    return "Starting…";
  }
  if (!pullRequest) {
    return "Create Pull Request";
  }
  switch (pullRequest.status) {
    case "queued":
      return "Queued…";
    case "creating":
      return "Creating…";
    case "failed":
      return "Try again";
    default:
      return "Create Pull Request";
  }
}

// Per-line color for a unified diff, by prefix only -- deliberately not a
// real diff parse (no hunk/file-boundary awareness), per the "keep it
// simple" steer for this view. This also colors the `+++`/`---` file-header
// lines the same as added/removed content lines, since both start with the
// same character -- an intentional simplification, not a bug.
function diffLineClass(line: string): string {
  if (line.startsWith("@@")) {
    return "text-sky-700 dark:text-sky-400";
  }
  if (line.startsWith("+")) {
    return "text-emerald-700 dark:text-emerald-400";
  }
  if (line.startsWith("-")) {
    return "text-red-700 dark:text-red-400";
  }
  return "text-zinc-800 dark:text-zinc-200";
}

// Renders a unified diff string readably: monospace, one `<span>` per line
// (so `<pre><code>` only ever contains phrasing content) colored by
// `diffLineClass`. `lines` is derived fresh from `diff` on every render and
// never reordered, so the array index is a stable, safe `key`.
function DiffView({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="max-h-[32rem] overflow-auto rounded-md bg-black/[.03] px-3 py-2 text-xs dark:bg-white/[.04]">
      <code>
        {lines.map((line, index) => (
          <span key={index} className={`block ${diffLineClass(line)}`}>
            {line.length > 0 ? line : " "}
          </span>
        ))}
      </code>
    </pre>
  );
}

// Mirrors `PlanView` above: status row first (same dot+label+relative-time
// convention as the page header), then a status-dependent content region
// (pending spinner / red error box / summary+diff), same layering as the
// planning_status handling in the main component below.
function CodeChangeView({ codeChange }: { codeChange: CodeChange }) {
  const isPending = isPendingGenerationStatus(codeChange.generation_status);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span
          aria-hidden
          className={`h-2 w-2 shrink-0 rounded-full ${
            GENERATION_STATUS_DOT_CLASS[codeChange.generation_status] ?? ""
          }`}
        />
        <span className="text-sm text-zinc-600 dark:text-zinc-400">
          {GENERATION_STATUS_LABEL[codeChange.generation_status] ??
            codeChange.generation_status}
          {" · "}
          <span
            title={formatAbsoluteTime(codeChange.created_at)}
            suppressHydrationWarning
          >
            {formatRelativeTime(codeChange.created_at)}
          </span>
        </span>
      </div>

      {codeChange.generation_status === "failed" ? (
        <div className="flex flex-col gap-2">
          <SectionHeading>Code generation failed</SectionHeading>
          <p className="whitespace-pre-wrap break-words rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400">
            {codeChange.generation_error ??
              "Something went wrong while generating code for this issue."}
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
            {codeChange.generation_status === "queued"
              ? "Queued for code generation…"
              : "Generating…"}{" "}
            This usually takes 15–60+ seconds.
          </span>
        </div>
      ) : codeChange.generation_status === "generated" ? (
        <div className="flex flex-col gap-3">
          {codeChange.summary ? (
            <p className="text-sm text-zinc-800 dark:text-zinc-200">
              {codeChange.summary}
            </p>
          ) : null}
          {codeChange.diff ? (
            <DiffView diff={codeChange.diff} />
          ) : (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No diff available.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

// Renders a captured stdout+stderr blob monospace, scrollable rather than
// truncated (output can be a full pytest/jest log) -- same box treatment as
// `DiffView` above, minus the per-line coloring since this isn't a diff.
// `tone` swaps the box's color to match the surrounding verdict: red for a
// failed run's log, neutral for a passed run's (the log itself isn't the
// alarming part when tests passed).
function OutputView({
  output,
  tone,
}: {
  output: string;
  tone: "neutral" | "red";
}) {
  return (
    <pre
      className={`max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md px-3 py-2 text-xs ${
        tone === "red"
          ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-400"
          : "bg-black/[.03] text-zinc-800 dark:bg-white/[.04] dark:text-zinc-200"
      }`}
    >
      <code>{output}</code>
    </pre>
  );
}

// Mirrors `CodeChangeView` above: status row first, then a
// status-dependent content region -- but with a three-way terminal split
// (passed/failed/error) instead of two, per the distinction called out on
// `TEST_RUN_STATUS_DOT_CLASS`.
function TestRunView({ testRun }: { testRun: TestRun }) {
  const isPending = isPendingTestRunStatus(testRun.status);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span
          aria-hidden
          className={`h-2 w-2 shrink-0 rounded-full ${
            TEST_RUN_STATUS_DOT_CLASS[testRun.status] ?? ""
          }`}
        />
        <span className="text-sm text-zinc-600 dark:text-zinc-400">
          {TEST_RUN_STATUS_LABEL[testRun.status] ?? testRun.status}
          {" · "}
          <span
            title={formatAbsoluteTime(testRun.created_at)}
            suppressHydrationWarning
          >
            {formatRelativeTime(testRun.created_at)}
          </span>
        </span>
      </div>

      {testRun.status === "error" ? (
        <div className="flex flex-col gap-2">
          <SectionHeading>No test verdict</SectionHeading>
          <p className="whitespace-pre-wrap break-words rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-800 dark:border-orange-900/50 dark:bg-orange-950/30 dark:text-orange-400">
            {testRun.output ??
              "Something went wrong before a test verdict could be reached."}
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
            {testRun.status === "queued"
              ? "Queued for test run…"
              : testRun.status === "fixing"
                ? `The Coder agent is attempting a fix (attempt ${testRun.fix_attempts} of ${MAX_FIX_ATTEMPTS})…`
                : "Running tests…"}{" "}
            This usually takes 30 seconds to several minutes.
          </span>
        </div>
      ) : testRun.status === "failed" ? (
        <div className="flex flex-col gap-2">
          <SectionHeading>Tests failed</SectionHeading>
          {testRun.fix_attempts > 0 ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              The Coder agent tried to fix this {testRun.fix_attempts}{" "}
              {testRun.fix_attempts === 1 ? "time" : "times"} and it still
              failed.
            </p>
          ) : null}
          {typeof testRun.exit_code === "number" ? (
            <p className="text-sm text-red-700 dark:text-red-400">
              Exit code {testRun.exit_code}
            </p>
          ) : null}
          {testRun.command ? (
            <p className="break-all font-mono text-xs text-zinc-600 dark:text-zinc-400">
              $ {testRun.command}
            </p>
          ) : null}
          {testRun.output ? (
            <OutputView output={testRun.output} tone="red" />
          ) : (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No output captured.
            </p>
          )}
        </div>
      ) : testRun.status === "passed" ? (
        <div className="flex flex-col gap-2">
          <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-400">
            All tests passed
            {testRun.fix_attempts > 0
              ? ` (after ${testRun.fix_attempts} fix ${
                  testRun.fix_attempts === 1 ? "attempt" : "attempts"
                } by the Coder agent).`
              : "."}
          </p>
          {testRun.command ? (
            <p className="break-all font-mono text-xs text-zinc-600 dark:text-zinc-400">
              $ {testRun.command}
            </p>
          ) : null}
          {testRun.output ? (
            <OutputView output={testRun.output} tone="neutral" />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// One review finding -- mirrors PlanView's `relevant_files` list item
// styling (bordered box, mono file path, description below), plus a
// severity badge so a "Blocking" comment reads differently from a
// "Suggestion" at a glance without needing its own color-coded box.
function ReviewCommentItem({ comment }: { comment: NonNullable<Review["comments"]>[number] }) {
  const isBlocking = comment.severity === "blocking";
  return (
    <li className="rounded-lg border border-black/[.08] px-4 py-3 dark:border-white/[.145]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="break-all font-mono text-sm font-medium text-zinc-950 dark:text-zinc-50">
          {comment.file_path}
        </span>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
            isBlocking
              ? "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-400"
              : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
          }`}
        >
          {isBlocking ? "Blocking" : "Suggestion"}
        </span>
      </div>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{comment.comment}</p>
    </li>
  );
}

// Mirrors `TestRunView` above: status row first, then a status-dependent
// content region -- three-way terminal split (approved/changes_requested/
// failed) per the distinction called out on `REVIEW_STATUS_DOT_CLASS`.
// Comments render for both approved and changes_requested (a clean
// approval usually has none, but a suggestion doesn't require requesting
// changes) -- only "failed" (the process itself broke) never has any.
function ReviewView({ review }: { review: Review }) {
  const isPending = isPendingReviewStatus(review.status);
  const isTerminalVerdict = review.status === "approved" || review.status === "changes_requested";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span
          aria-hidden
          className={`h-2 w-2 shrink-0 rounded-full ${
            REVIEW_STATUS_DOT_CLASS[review.status] ?? ""
          }`}
        />
        <span className="text-sm text-zinc-600 dark:text-zinc-400">
          {REVIEW_STATUS_LABEL[review.status] ?? review.status}
          {" · "}
          <span title={formatAbsoluteTime(review.created_at)} suppressHydrationWarning>
            {formatRelativeTime(review.created_at)}
          </span>
        </span>
      </div>

      {review.status === "failed" ? (
        <div className="flex flex-col gap-2">
          <SectionHeading>Review failed</SectionHeading>
          <p className="whitespace-pre-wrap break-words rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400">
            {review.error ?? "Something went wrong while reviewing this change."}
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
            {review.status === "queued" ? "Queued for review…" : "Reviewing…"} This usually
            takes 10–45 seconds.
          </span>
        </div>
      ) : isTerminalVerdict ? (
        <div className="flex flex-col gap-3">
          <p
            className={`rounded-lg border px-4 py-3 text-sm ${
              review.status === "approved"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-400"
                : "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-400"
            }`}
          >
            {review.summary}
          </p>
          {review.comments && review.comments.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {review.comments.map((comment, index) => (
                <ReviewCommentItem key={index} comment={comment} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// Mirrors `ReviewView` above: status row first, then a status-dependent
// content region -- but a single terminal failure state instead of a
// three-way split (see `PR_STATUS_DOT_CLASS`'s comment for why). "created"
// gets the most prominent treatment of any terminal state on this page: a
// real, clickable link to the actual PR, since that link is the entire
// point of this stage.
function PullRequestView({ pullRequest }: { pullRequest: PullRequest }) {
  const isPending = isPendingPRStatus(pullRequest.status);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span
          aria-hidden
          className={`h-2 w-2 shrink-0 rounded-full ${
            PR_STATUS_DOT_CLASS[pullRequest.status] ?? ""
          }`}
        />
        <span className="text-sm text-zinc-600 dark:text-zinc-400">
          {PR_STATUS_LABEL[pullRequest.status] ?? pullRequest.status}
          {" · "}
          <span title={formatAbsoluteTime(pullRequest.created_at)} suppressHydrationWarning>
            {formatRelativeTime(pullRequest.created_at)}
          </span>
        </span>
      </div>

      {pullRequest.status === "failed" ? (
        <div className="flex flex-col gap-2">
          <SectionHeading>Pull request creation failed</SectionHeading>
          <p className="whitespace-pre-wrap break-words rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400">
            {pullRequest.error ?? "Something went wrong while creating the pull request."}
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
            {pullRequest.status === "queued"
              ? "Queued…"
              : "Pushing your changes and opening a pull request…"}{" "}
            This usually takes a few seconds.
          </span>
        </div>
      ) : pullRequest.status === "created" && pullRequest.pr_url ? (
        <a
          href={pullRequest.pr_url}
          target="_blank"
          rel="noreferrer"
          className="flex flex-col gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 transition-colors hover:bg-emerald-100 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-400 dark:hover:bg-emerald-950/50"
        >
          <span className="font-medium">
            View pull request{pullRequest.pr_number ? ` #${pullRequest.pr_number}` : ""} on
            GitHub ↗
          </span>
          {pullRequest.branch_name ? (
            <span className="break-all font-mono text-xs opacity-80">
              {pullRequest.branch_name}
            </span>
          ) : null}
        </a>
      ) : null}
    </div>
  );
}

const USAGE_AGENT_LABEL: Record<string, string> = {
  planner: "Planner",
  coder: "Coder",
  reviewer: "Reviewer",
};

// Costs here are fractions of a cent in practice -- 4 decimal places is
// enough precision to show a real nonzero number instead of a
// rounded-away "$0.00" for a normal-sized call.
function formatUsd(amount: number): string {
  return `$${amount.toFixed(4)}`;
}

// Purely informational -- no button, no pending/error state of its own (a
// failed fetch just means the strip doesn't render; this is supplementary
// context, not a pipeline stage the user drives). Renders nothing once
// there's genuinely nothing to show yet (a fresh issue with no agent
// calls made), rather than a "$0.0000" that would just be noise before
// the Planner has even run.
function UsageSummaryView({ summary }: { summary: UsageSummary }) {
  if (summary.total_input_tokens === 0 && summary.total_output_tokens === 0) {
    return null;
  }
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-black/[.08] bg-white px-4 py-3 dark:border-white/[.145] dark:bg-zinc-900">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-zinc-950 dark:text-zinc-50">
          AI usage (estimated)
        </span>
        <span className="font-mono text-sm text-zinc-600 dark:text-zinc-400">
          {formatUsd(summary.total_estimated_cost_usd)}
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
        {summary.by_agent.map((agent) => (
          <span key={agent.agent}>
            {USAGE_AGENT_LABEL[agent.agent] ?? agent.agent}:{" "}
            {(agent.input_tokens + agent.output_tokens).toLocaleString()} tokens (
            {formatUsd(agent.estimated_cost_usd)})
          </span>
        ))}
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

  // --- AI usage (Milestone 11 cost tracking) ---
  // Purely derived, read-only context -- no request-id guard like
  // loadIssue's, since a stray stale response here would only make this
  // one supplementary strip flicker to an older number for a moment, not
  // show wrong data about which pipeline stage the issue is in. Refetched
  // whenever `issue` changes so it stays in sync with however many agent
  // calls have happened so far, including mid-poll.
  const [usage, setUsage] = useState<UsageSummary | null>(null);

  useEffect(() => {
    if (!issue) {
      return;
    }
    apiFetch<UsageSummary>(
      `/api/repositories/${repositoryId}/issues/${issueId}/usage`,
    )
      .then(setUsage)
      .catch(() => {
        // Non-critical: leave whatever was last shown (or nothing) rather
        // than surface an error for a purely supplementary metric.
      });
  }, [issue, repositoryId, issueId]);

  // --- Code generation (Coder agent) ---
  const [generateRequesting, setGenerateRequesting] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const handleGenerateCode = useCallback(async () => {
    // A code_change already sitting in a terminal state means this call
    // discards it and starts over (the backend keeps no history of past
    // attempts) -- gate that specifically so a regenerate is a deliberate
    // choice, not a stray click. A first-time generate (no code_change yet)
    // skips this prompt entirely.
    const codeChange = issue?.code_change ?? null;
    const isRegenerate =
      codeChange !== null &&
      (codeChange.generation_status === "generated" ||
        codeChange.generation_status === "failed");
    if (
      isRegenerate &&
      !window.confirm(
        "Regenerate code for this issue? This replaces the current diff and can't be undone.",
      )
    ) {
      return;
    }

    setGenerateRequesting(true);
    setGenerateError(null);
    try {
      const updated = await apiFetch<Issue>(
        `/api/repositories/${repositoryId}/issues/${issueId}/code-changes`,
        { method: "POST" },
      );
      setIssue(updated);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Someone else (another tab, or a race) already has generation
        // queued/in progress, or the plan isn't ready after all -- resync
        // rather than show a stale error, same convention as the index
        // button's 409 handling in repositories/page.tsx.
        await loadIssue();
      } else if (error instanceof ApiError && error.status === 429) {
        // The endpoint is rate-limited to 10 req/min -- call that out
        // specifically, same convention as the issue-submit and search
        // forms' 429 handling.
        setGenerateError(
          "Too many code generation requests — wait a moment and try again.",
        );
      } else {
        setGenerateError("Couldn't start code generation, try again.");
      }
    } finally {
      setGenerateRequesting(false);
    }
  }, [issue, repositoryId, issueId, loadIssue]);

  // --- Test run (Sandbox Test Runner) ---
  const [testRunRequesting, setTestRunRequesting] = useState(false);
  const [testRunError, setTestRunError] = useState<string | null>(null);

  const handleRunTests = useCallback(async () => {
    // Unlike `handleGenerateCode`'s regenerate, this deliberately skips a
    // confirm() gate: re-running tests doesn't discard anything the user
    // authored or would mind losing, it just re-executes the same diff
    // against the same test suite again and replaces a derived result --
    // a lot less destructive than discarding a generated diff.
    setTestRunRequesting(true);
    setTestRunError(null);
    try {
      const updated = await apiFetch<Issue>(
        `/api/repositories/${repositoryId}/issues/${issueId}/test-runs`,
        { method: "POST" },
      );
      setIssue(updated);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Same reasoning as handleGenerateCode's 409 handling: resync
        // instead of showing a stale error.
        await loadIssue();
      } else if (error instanceof ApiError && error.status === 429) {
        setTestRunError(
          "Too many test run requests — wait a moment and try again.",
        );
      } else {
        setTestRunError("Couldn't start the test run, try again.");
      }
    } finally {
      setTestRunRequesting(false);
    }
  }, [repositoryId, issueId, loadIssue]);

  // --- Code review (Reviewer agent) ---
  const [reviewRequesting, setReviewRequesting] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const handleRequestReview = useCallback(async () => {
    // No confirm() gate, same reasoning as handleRunTests: re-requesting a
    // review doesn't discard anything the user authored, it just re-reviews
    // the same, unchanged diff and replaces a derived verdict.
    setReviewRequesting(true);
    setReviewError(null);
    try {
      const updated = await apiFetch<Issue>(
        `/api/repositories/${repositoryId}/issues/${issueId}/reviews`,
        { method: "POST" },
      );
      setIssue(updated);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Same reasoning as the other handlers' 409 handling: resync
        // instead of showing a stale error.
        await loadIssue();
      } else if (error instanceof ApiError && error.status === 429) {
        setReviewError(
          "Too many review requests — wait a moment and try again.",
        );
      } else {
        setReviewError("Couldn't request a review, try again.");
      }
    } finally {
      setReviewRequesting(false);
    }
  }, [repositoryId, issueId, loadIssue]);

  // --- Pull request (human approval + GitHub PR creation) ---
  const [pullRequestRequesting, setPullRequestRequesting] = useState(false);
  const [pullRequestError, setPullRequestError] = useState<string | null>(null);

  const handleCreatePullRequest = useCallback(async () => {
    // The one confirm() gate on this page that isn't about discarding
    // data: this is the single most consequential action here, the only
    // one with a real, external side effect (a real branch pushed and a
    // real PR opened on GitHub, visible to anyone with repo access) --
    // unlike every other button, which only ever mutates CodePilot's own
    // data. Clicking through this dialog *is* the "Human Approval" the
    // pipeline is named for; there's no separate approve step before it.
    const reviewStatus = issue?.code_change?.review?.status ?? null;
    const confirmMessage =
      reviewStatus === "changes_requested"
        ? "The AI review requested changes for this code. Create a pull request anyway? " +
          "This pushes a new branch and opens a real pull request on GitHub."
        : "Create a pull request for this code? This pushes a new branch and opens a real " +
          "pull request on GitHub.";
    if (!window.confirm(confirmMessage)) {
      return;
    }

    setPullRequestRequesting(true);
    setPullRequestError(null);
    try {
      const updated = await apiFetch<Issue>(
        `/api/repositories/${repositoryId}/issues/${issueId}/pull-requests`,
        { method: "POST" },
      );
      setIssue(updated);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Same reasoning as the other handlers' 409 handling: resync
        // instead of showing a stale error.
        await loadIssue();
      } else if (error instanceof ApiError && error.status === 429) {
        setPullRequestError(
          "Too many pull request requests — wait a moment and try again.",
        );
      } else {
        setPullRequestError("Couldn't create the pull request, try again.");
      }
    } finally {
      setPullRequestRequesting(false);
    }
  }, [issue, repositoryId, issueId, loadIssue]);

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
  const isGenerationPending = issue?.code_change
    ? isPendingGenerationStatus(issue.code_change.generation_status)
    : false;
  const isTestRunPending = issue?.code_change?.test_run
    ? isPendingTestRunStatus(issue.code_change.test_run.status)
    : false;
  const isReviewPending = issue?.code_change?.review
    ? isPendingReviewStatus(issue.code_change.review.status)
    : false;
  const isPullRequestPending = issue?.code_change?.pull_request
    ? isPendingPRStatus(issue.code_change.pull_request.status)
    : false;

  // Poll while the issue is queued/planning, its code_change is
  // queued/generating, its test_run is queued/running/fixing, its review
  // is queued/reviewing, or its pull_request is queued/creating, so any of
  // the five resolves without a manual refresh -- stopping once none is
  // pending any more. One shared interval (rather than a fifth one scoped
  // to pull_request) since all five conditions just mean "re-fetch this
  // same issue"; `loadIssue` itself already guards against out-of-order
  // responses via `issueRequestId`. Same pattern as the list page's poll,
  // scoped to this one issue instead of a list.
  useEffect(() => {
    if (
      !isPending &&
      !isGenerationPending &&
      !isTestRunPending &&
      !isReviewPending &&
      !isPullRequestPending
    ) {
      return;
    }
    const intervalId = setInterval(() => {
      loadIssue();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [
    isPending,
    isGenerationPending,
    isTestRunPending,
    isReviewPending,
    isPullRequestPending,
    loadIssue,
  ]);

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

            {usage ? <UsageSummaryView summary={usage} /> : null}

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

            {issue.planning_status === "planned" ? (
              <div className="flex flex-col gap-4 border-t border-black/[.08] pt-6 dark:border-white/[.145]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SectionHeading>Code generation</SectionHeading>
                  <button
                    type="button"
                    onClick={handleGenerateCode}
                    disabled={generateRequesting || isGenerationPending}
                    className="shrink-0 rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:cursor-default disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
                  >
                    {generateButtonLabel(issue.code_change, generateRequesting)}
                  </button>
                </div>

                {generateError ? (
                  <p className="text-sm text-red-600 dark:text-red-400">
                    {generateError}
                  </p>
                ) : null}

                {issue.code_change ? (
                  <CodeChangeView codeChange={issue.code_change} />
                ) : (
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">
                    No code has been generated for this issue yet.
                  </p>
                )}
              </div>
            ) : null}

            {issue.code_change?.generation_status === "generated" ? (
              <div className="flex flex-col gap-4 border-t border-black/[.08] pt-6 dark:border-white/[.145]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SectionHeading>Test run</SectionHeading>
                  <button
                    type="button"
                    onClick={handleRunTests}
                    disabled={testRunRequesting || isTestRunPending}
                    className="shrink-0 rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:cursor-default disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
                  >
                    {testRunButtonLabel(issue.code_change.test_run, testRunRequesting)}
                  </button>
                </div>

                {testRunError ? (
                  <p className="text-sm text-red-600 dark:text-red-400">
                    {testRunError}
                  </p>
                ) : null}

                {issue.code_change.test_run ? (
                  <TestRunView testRun={issue.code_change.test_run} />
                ) : (
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">
                    No tests have been run for this issue yet.
                  </p>
                )}
              </div>
            ) : null}

            {issue.code_change?.test_run?.status === "passed" ? (
              <div className="flex flex-col gap-4 border-t border-black/[.08] pt-6 dark:border-white/[.145]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SectionHeading>Code review</SectionHeading>
                  <button
                    type="button"
                    onClick={handleRequestReview}
                    disabled={reviewRequesting || isReviewPending}
                    className="shrink-0 rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:cursor-default disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
                  >
                    {reviewButtonLabel(issue.code_change.review, reviewRequesting)}
                  </button>
                </div>

                {reviewError ? (
                  <p className="text-sm text-red-600 dark:text-red-400">
                    {reviewError}
                  </p>
                ) : null}

                {issue.code_change.review ? (
                  <ReviewView review={issue.code_change.review} />
                ) : (
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">
                    No review has been requested for this issue yet.
                  </p>
                )}
              </div>
            ) : null}

            {issue.code_change?.test_run?.status === "passed" ? (
              <div className="flex flex-col gap-4 border-t border-black/[.08] pt-6 dark:border-white/[.145]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SectionHeading>Pull request</SectionHeading>
                  {issue.code_change.pull_request?.status !== "created" ? (
                    <button
                      type="button"
                      onClick={handleCreatePullRequest}
                      disabled={pullRequestRequesting || isPullRequestPending}
                      className="shrink-0 rounded-md bg-zinc-950 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 disabled:cursor-default disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
                    >
                      {pullRequestButtonLabel(issue.code_change.pull_request, pullRequestRequesting)}
                    </button>
                  ) : null}
                </div>

                {pullRequestError ? (
                  <p className="text-sm text-red-600 dark:text-red-400">
                    {pullRequestError}
                  </p>
                ) : null}

                {issue.code_change.pull_request ? (
                  <PullRequestView pullRequest={issue.code_change.pull_request} />
                ) : (
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">
                    No pull request has been created for this issue yet.
                  </p>
                )}
              </div>
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}
