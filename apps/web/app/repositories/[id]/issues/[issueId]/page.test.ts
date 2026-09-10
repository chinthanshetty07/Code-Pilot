import type { CodeChange, PullRequest, Review, TestRun } from "@codepilot/shared-types";
import { describe, expect, it } from "vitest";
import {
  diffLineClass,
  formatUsd,
  generateButtonLabel,
  pullRequestButtonLabel,
  reviewButtonLabel,
  testRunButtonLabel,
} from "./page";

function makeCodeChange(overrides: Partial<CodeChange> = {}): CodeChange {
  return {
    id: "cc-1",
    generation_status: "generated",
    generation_error: null,
    summary: null,
    diff: null,
    created_at: "2026-01-01T00:00:00.000Z",
    test_run: null,
    review: null,
    pull_request: null,
    ...overrides,
  };
}

function makeTestRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: "tr-1",
    status: "passed",
    command: "pytest",
    output: null,
    exit_code: 0,
    fix_attempts: 0,
    created_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function makeReview(overrides: Partial<Review> = {}): Review {
  return {
    id: "rv-1",
    status: "approved",
    error: null,
    summary: null,
    comments: null,
    created_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function makePullRequest(overrides: Partial<PullRequest> = {}): PullRequest {
  return {
    id: "pr-1",
    status: "created",
    error: null,
    branch_name: "codepilot/example-12345678",
    pr_number: 1,
    pr_url: "https://github.com/owner/repo/pull/1",
    created_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

describe("generateButtonLabel", () => {
  it("shows 'Starting…' while in flight, regardless of any other state", () => {
    expect(generateButtonLabel(null, true)).toBe("Starting…");
    expect(generateButtonLabel(makeCodeChange({ generation_status: "failed" }), true)).toBe(
      "Starting…",
    );
  });

  it("invites first-time generation when there's no code_change yet", () => {
    expect(generateButtonLabel(null, false)).toBe("Generate code");
  });

  it("reflects queued/generating in-progress states", () => {
    expect(
      generateButtonLabel(makeCodeChange({ generation_status: "queued" }), false),
    ).toBe("Queued…");
    expect(
      generateButtonLabel(makeCodeChange({ generation_status: "generating" }), false),
    ).toBe("Generating…");
  });

  it("offers 'Regenerate' once generated or after a failure", () => {
    expect(
      generateButtonLabel(makeCodeChange({ generation_status: "generated" }), false),
    ).toBe("Regenerate");
    expect(
      generateButtonLabel(makeCodeChange({ generation_status: "failed" }), false),
    ).toBe("Regenerate");
  });
});

describe("testRunButtonLabel", () => {
  it("invites a first run when there's no test_run yet", () => {
    expect(testRunButtonLabel(null, false)).toBe("Run tests");
  });

  it("reflects queued/running/fixing in-progress states", () => {
    expect(testRunButtonLabel(makeTestRun({ status: "queued" }), false)).toBe("Queued…");
    expect(testRunButtonLabel(makeTestRun({ status: "running" }), false)).toBe("Running…");
    expect(testRunButtonLabel(makeTestRun({ status: "fixing" }), false)).toBe("Fixing…");
  });

  it.each(["passed", "failed", "error"])(
    "offers 'Run tests again' for any terminal status (%s)",
    (status) => {
      expect(testRunButtonLabel(makeTestRun({ status }), false)).toBe("Run tests again");
    },
  );
});

describe("reviewButtonLabel", () => {
  it("invites a first review when there's no review yet", () => {
    expect(reviewButtonLabel(null, false)).toBe("Request review");
  });

  it.each(["approved", "changes_requested", "failed"])(
    "offers 'Request review again' for any terminal status (%s)",
    (status) => {
      expect(reviewButtonLabel(makeReview({ status }), false)).toBe("Request review again");
    },
  );
});

describe("pullRequestButtonLabel", () => {
  it("invites creating a PR when there's none yet", () => {
    expect(pullRequestButtonLabel(null, false)).toBe("Create Pull Request");
  });

  it("offers 'Try again' after a failed attempt specifically (not the generic label)", () => {
    expect(pullRequestButtonLabel(makePullRequest({ status: "failed" }), false)).toBe(
      "Try again",
    );
  });

  it("reflects queued/creating in-progress states", () => {
    expect(pullRequestButtonLabel(makePullRequest({ status: "queued" }), false)).toBe(
      "Queued…",
    );
    expect(pullRequestButtonLabel(makePullRequest({ status: "creating" }), false)).toBe(
      "Creating…",
    );
  });
});

describe("diffLineClass", () => {
  it("colors a hunk header line", () => {
    expect(diffLineClass("@@ -1,2 +1,2 @@")).toBe("text-sky-700 dark:text-sky-400");
  });

  it("colors an added line", () => {
    expect(diffLineClass("+new line")).toBe("text-emerald-700 dark:text-emerald-400");
  });

  it("colors a removed line", () => {
    expect(diffLineClass("-old line")).toBe("text-red-700 dark:text-red-400");
  });

  it("colors an unrelated context line neutrally", () => {
    expect(diffLineClass(" unchanged line")).toBe("text-zinc-800 dark:text-zinc-200");
  });
});

describe("formatUsd", () => {
  it("formats a whole-dollar amount with 4 decimal places", () => {
    expect(formatUsd(1)).toBe("$1.0000");
  });

  it("keeps small fractional-cent amounts visible instead of rounding to $0.00", () => {
    expect(formatUsd(0.00034)).toBe("$0.0003");
  });

  it("formats zero", () => {
    expect(formatUsd(0)).toBe("$0.0000");
  });
});
